"""
Create raw ``.dd`` disk images with bad-sector resilience, resumability, and
SHA-256 verification.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import BinaryIO, Callable, List, Optional, Tuple

from config.scan_settings import (
    IMAGE_CHECKPOINT_INTERVAL_SECONDS,
    IMAGE_READ_CHUNK_SIZE,
    IMAGE_SECTOR_FALLBACK_SIZE,
)
from models.disk_image import DiskImageRecord
from utils.atomic_io import write_json_atomic
from utils.device_io import open_device

logger = logging.getLogger(__name__)

CHECKPOINT_SUFFIX = ".progress.json"


class DiskImageWriter:
    """
    Read-only imaging of a block device into a raw image file.

    Writes sequentially and reports progress via callback. Unreadable source
    ranges (bad sectors) are isolated at sector granularity, zero-filled in the
    image, and recorded rather than aborting the whole job. Progress is
    checkpointed so an interrupted imaging run can be resumed instead of
    restarted from byte zero.
    """

    def create_image(
        self,
        source_device: str,
        destination_path: str,
        size_bytes: Optional[int] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
        resume: bool = False,
    ) -> Tuple[DiskImageRecord, str]:
        """
        Copy a block device to a raw image file.

        Args:
            source_device: Source block device path (e.g. ``/dev/sda``).
            destination_path: Output ``.dd`` file path.
            size_bytes: Optional byte limit (defaults to full device size).
            on_progress: Callback(bytes_done, bytes_total, status_text).
            should_cancel: Returns True to abort imaging (progress is checkpointed
                first, so a subsequent call with ``resume=True`` continues).
            resume: When True, continue from a previous checkpoint for this exact
                destination path if one exists; otherwise start fresh.

        Returns:
            Tuple of (DiskImageRecord, absolute output path).

        Raises:
            OSError: When source or destination cannot be accessed.
            RuntimeError: When imaging is cancelled.
        """
        os.makedirs(os.path.dirname(destination_path) or ".", exist_ok=True)
        checkpoint_path = self._checkpoint_path(destination_path)

        hasher = hashlib.sha256()
        processed = 0
        bad_ranges: List[Tuple[int, int]] = []
        checkpoint = self._load_checkpoint(checkpoint_path) if resume else None

        with open_device(source_device) as source:
            if size_bytes:
                total = size_bytes
            elif checkpoint:
                total = checkpoint.get("total") or self._detect_size(source)
            else:
                total = self._detect_size(source)

            destination, processed, bad_ranges = self._open_destination(
                destination_path, checkpoint, total
            )
            try:
                if processed:
                    self._rehash_existing(destination_path, processed, hasher)

                last_checkpoint = time.monotonic()
                while processed < total:
                    if should_cancel and should_cancel():
                        self._save_checkpoint(checkpoint_path, source_device, destination_path, total, processed, bad_ranges)
                        raise RuntimeError("Disk imaging cancelled")

                    chunk_size = min(IMAGE_READ_CHUNK_SIZE, total - processed)
                    chunk = self._read_resilient(source, processed, chunk_size, bad_ranges)

                    destination.write(chunk)
                    hasher.update(chunk)
                    processed += len(chunk)

                    now = time.monotonic()
                    if now - last_checkpoint >= IMAGE_CHECKPOINT_INTERVAL_SECONDS:
                        destination.flush()
                        os.fsync(destination.fileno())
                        self._save_checkpoint(
                            checkpoint_path, source_device, destination_path, total, processed, bad_ranges
                        )
                        last_checkpoint = now

                    if on_progress:
                        status = f"Imaging {os.path.basename(source_device)}"
                        if bad_ranges:
                            status += f" ({len(bad_ranges)} bad sector range(s))"
                        on_progress(processed, total, status)
            finally:
                destination.close()

        self._remove_checkpoint(checkpoint_path)

        if bad_ranges:
            logger.warning(
                "Imaging %s completed with %d unreadable sector range(s), zero-filled in the image",
                source_device,
                len(bad_ranges),
            )

        record = DiskImageRecord(
            image_id=os.path.splitext(os.path.basename(destination_path))[0],
            file_path=os.path.abspath(destination_path),
            source_device=source_device,
            size_bytes=processed,
            sha256=hasher.hexdigest(),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            label=os.path.basename(destination_path),
            bad_sector_ranges=bad_ranges,
        )
        return record, os.path.abspath(destination_path)

    def _read_resilient(
        self,
        source: BinaryIO,
        absolute_offset: int,
        length: int,
        bad_ranges: List[Tuple[int, int]],
    ) -> bytes:
        """
        Read ``length`` bytes at ``absolute_offset``, always returning exactly
        ``length`` bytes.

        A normal read failure (or short read) falls back to sector-sized reads so
        only the actually-unreadable sectors are zero-filled and logged in
        ``bad_ranges`` — the rest of the chunk is still recovered, instead of
        discarding a whole multi-megabyte chunk for one bad sector.
        """
        source.seek(absolute_offset)
        try:
            data = source.read(length)
            if len(data) == length:
                return data
        except OSError:
            data = b""

        result = bytearray(data)
        cursor = absolute_offset + len(result)
        remaining = length - len(result)
        while remaining > 0:
            step = min(IMAGE_SECTOR_FALLBACK_SIZE, remaining)
            try:
                source.seek(cursor)
                sector = source.read(step)
            except OSError:
                sector = b""
            if len(sector) < step:
                bad_ranges.append((cursor, step))
                sector = b"\x00" * step
            result.extend(sector)
            cursor += step
            remaining -= step
        return bytes(result)

    def _open_destination(
        self,
        destination_path: str,
        checkpoint: Optional[dict],
        total: int,
    ) -> Tuple[BinaryIO, int, List[Tuple[int, int]]]:
        """
        Open the destination file for a fresh or resumed imaging run.

        Returns:
            Tuple of (open file handle positioned to append, processed bytes
            already written, bad ranges recorded so far).
        """
        if checkpoint and os.path.isfile(destination_path):
            existing_size = os.path.getsize(destination_path)
            processed = min(int(checkpoint.get("processed", 0)), existing_size, total)
            bad_ranges = [tuple(entry) for entry in checkpoint.get("bad_ranges", [])]
            destination = open(destination_path, "r+b")
            destination.seek(processed)
            destination.truncate(processed)
            logger.info("Resuming disk image %s from byte %d/%d", destination_path, processed, total)
            return destination, processed, bad_ranges

        destination = open(destination_path, "wb")
        return destination, 0, []

    def _rehash_existing(self, destination_path: str, processed: int, hasher) -> None:
        """Re-hash the already-written prefix of a resumed image to keep the digest correct."""
        with open(destination_path, "rb") as handle:
            remaining = processed
            while remaining > 0:
                chunk = handle.read(min(IMAGE_READ_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                hasher.update(chunk)
                remaining -= len(chunk)

    @staticmethod
    def _detect_size(source) -> int:
        """Detect readable size of an open block device handle."""
        source.seek(0, os.SEEK_END)
        size = source.tell()
        source.seek(0)
        if size <= 0:
            raise OSError("Could not determine source device size")
        return size

    @staticmethod
    def _checkpoint_path(destination_path: str) -> str:
        """Sidecar path storing in-progress imaging state for resumability."""
        return destination_path + CHECKPOINT_SUFFIX

    @staticmethod
    def has_resumable_checkpoint(destination_path: str) -> bool:
        """Return True when an interrupted imaging run can be resumed for this destination."""
        return os.path.isfile(DiskImageWriter._checkpoint_path(destination_path))

    @staticmethod
    def _load_checkpoint(checkpoint_path: str) -> Optional[dict]:
        """Load a previously saved imaging checkpoint, if any."""
        if not os.path.isfile(checkpoint_path):
            return None
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Could not load imaging checkpoint %s: %s", checkpoint_path, exc)
            return None

    @staticmethod
    def _save_checkpoint(
        checkpoint_path: str,
        source_device: str,
        destination_path: str,
        total: int,
        processed: int,
        bad_ranges: List[Tuple[int, int]],
    ) -> None:
        """Persist imaging progress atomically so a crash loses at most one interval."""
        payload = {
            "source_device": source_device,
            "destination_path": destination_path,
            "total": total,
            "processed": processed,
            "bad_ranges": [[offset, length] for offset, length in bad_ranges],
        }
        try:
            write_json_atomic(checkpoint_path, payload)
        except OSError as exc:
            logger.warning("Could not save imaging checkpoint %s: %s", checkpoint_path, exc)

    @staticmethod
    def _remove_checkpoint(checkpoint_path: str) -> None:
        """Remove the checkpoint file after imaging completes successfully."""
        try:
            if os.path.isfile(checkpoint_path):
                os.remove(checkpoint_path)
        except OSError as exc:
            logger.debug("Could not remove imaging checkpoint %s: %s", checkpoint_path, exc)

    @staticmethod
    def verify_image(image_path: str, expected_sha256: str) -> bool:
        """
        Verify an image file against an expected SHA-256 hash.

        Args:
            image_path: Path to the image file.
            expected_sha256: Expected lowercase hex digest.

        Returns:
            True when the hash matches.
        """
        hasher = hashlib.sha256()
        with open(image_path, "rb") as handle:
            while True:
                chunk = handle.read(IMAGE_READ_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest().lower() == expected_sha256.lower()
