"""
Create raw ``.dd`` disk images with optional SHA-256 verification.
"""

import hashlib
import logging
import os
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from config.scan_settings import IMAGE_READ_CHUNK_SIZE
from models.disk_image import DiskImageRecord

logger = logging.getLogger(__name__)


class DiskImageWriter:
    """
    Read-only imaging of a block device into a raw image file.

    Writes sequentially and reports progress via callback.
    """

    def create_image(
        self,
        source_device: str,
        destination_path: str,
        size_bytes: Optional[int] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[DiskImageRecord, str]:
        """
        Copy a block device to a raw image file.

        Args:
            source_device: Source block device path (e.g. ``/dev/sda``).
            destination_path: Output ``.dd`` file path.
            size_bytes: Optional byte limit (defaults to full device size).
            on_progress: Callback(bytes_done, bytes_total, status_text).
            should_cancel: Returns True to abort imaging.

        Returns:
            Tuple of (DiskImageRecord, absolute output path).

        Raises:
            OSError: When source or destination cannot be accessed.
            RuntimeError: When imaging is cancelled.
        """
        hasher = hashlib.sha256()
        processed = 0

        with open(source_device, "rb") as source:
            total = size_bytes or self._detect_size(source)
            os.makedirs(os.path.dirname(destination_path) or ".", exist_ok=True)

            with open(destination_path, "wb") as destination:
                while processed < total:
                    if should_cancel and should_cancel():
                        raise RuntimeError("Disk imaging cancelled")

                    chunk_size = min(IMAGE_READ_CHUNK_SIZE, total - processed)
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break

                    destination.write(chunk)
                    hasher.update(chunk)
                    processed += len(chunk)

                    if on_progress:
                        on_progress(
                            processed,
                            total,
                            f"Imaging {os.path.basename(source_device)}",
                        )

        record = DiskImageRecord(
            image_id=os.path.splitext(os.path.basename(destination_path))[0],
            file_path=os.path.abspath(destination_path),
            source_device=source_device,
            size_bytes=processed,
            sha256=hasher.hexdigest(),
            created_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            label=os.path.basename(destination_path),
        )
        return record, os.path.abspath(destination_path)

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
