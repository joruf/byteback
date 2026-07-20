"""
File signature carving for unallocated and raw block-device regions.
"""

import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field
from typing import BinaryIO, Callable, Dict, List, Optional, Set, Tuple

from config import FILE_SIGNATURES, RAW_READ_CHUNK_SIZE
from config.storage_paths import PREVIEW_DIR_NAME
from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget
from services.carving.format_parsers import detect_file_size, validate_carved_file

logger = logging.getLogger(__name__)

# Signatures considered reliable enough for quick-scan mode (footer or parser).
QUICK_SCAN_SIGNATURES = {
    "JPEG Image",
    "PNG Image",
    "PDF Document",
}


@dataclass
class PendingCarve:
    """Tracks a header match waiting for a footer in subsequent buffer data."""

    label: str
    signature: dict
    absolute_offset: int
    header_index: int
    extension: str


@dataclass
class CarveContext:
    """Shared carving state for one device read session."""

    device: BinaryIO
    device_path: str
    source_target_id: str
    preview_dir: str
    seen_offsets: Set[int] = field(default_factory=set)
    pending: List[PendingCarve] = field(default_factory=list)
    deep_scan: bool = True


class FileCarver:
    """
    Scans a byte range on a block device and extracts files by magic-byte signatures.

    Carved file content is written to a temporary preview directory so the user
    can inspect results before final recovery.
    """

    def __init__(self, preview_dir: Optional[str] = None) -> None:
        """
        Args:
            preview_dir: Directory for carved preview files (created if missing).
        """
        self.preview_dir = preview_dir or os.path.join(
            tempfile.gettempdir(),
            PREVIEW_DIR_NAME,
        )
        os.makedirs(self.preview_dir, exist_ok=True)

    def carve_range(
        self,
        target: StorageTarget,
        start_offset: int,
        size_bytes: int,
        source_target_id: str,
        start_from: int = 0,
        deep_scan: bool = True,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[RecoveryEntry], int]:
        """
        Carve recoverable files from a byte range on a block device.

        Args:
            target: Storage target providing the device path.
            start_offset: Absolute offset on the device where the range begins.
            size_bytes: Number of bytes to scan.
            source_target_id: ID stored on each RecoveryEntry.
            start_from: Resume offset within the range (relative, not absolute).
            deep_scan: When False, only high-confidence signatures are used.
            on_entry: Callback invoked for each carved file.
            on_progress: Callback(bytes_done, bytes_total, current_description).
            should_pause: Returns True when the worker should pause.
            should_cancel: Returns True when the worker should stop.

        Returns:
            Tuple of (discovered entries, final relative offset).
        """
        entries: List[RecoveryEntry] = []
        device_path = target.device_path
        overlap = max(
            len(sig["header"]) + int(sig.get("header_offset", 0))
            for sig in FILE_SIGNATURES.values()
        )
        buffer = b""
        processed = start_from

        try:
            with open(device_path, "rb") as device:
                device.seek(start_offset + start_from)
                context = CarveContext(
                    device=device,
                    device_path=device_path,
                    source_target_id=source_target_id,
                    preview_dir=self.preview_dir,
                    deep_scan=deep_scan,
                )

                while processed < size_bytes:
                    if should_cancel and should_cancel():
                        break

                    while should_pause and should_pause():
                        if should_cancel and should_cancel():
                            return entries, processed
                        import time

                        time.sleep(0.2)

                    read_size = min(RAW_READ_CHUNK_SIZE, size_bytes - processed)
                    chunk = device.read(read_size)
                    if not chunk:
                        break

                    buffer += chunk
                    processed += len(chunk)
                    buffer_base = start_offset + processed - len(buffer)

                    if on_progress:
                        on_progress(
                            processed,
                            size_bytes,
                            f"Carving @ {StorageTarget.format_size(start_offset + processed)}",
                        )

                    new_entries, buffer = self._process_buffer(
                        buffer=buffer,
                        base_offset=buffer_base,
                        overlap=overlap,
                        context=context,
                    )
                    for entry in new_entries:
                        entries.append(entry)
                        if on_entry:
                            on_entry(entry)

                    if len(buffer) > overlap * 4:
                        buffer = buffer[-overlap * 2 :]

        except PermissionError:
            logger.error("Permission denied reading %s (root may be required)", device_path)
            raise
        except OSError as exc:
            logger.error("Error carving %s: %s", device_path, exc)
            raise

        return entries, processed

    def _process_buffer(
        self,
        buffer: bytes,
        base_offset: int,
        overlap: int,
        context: CarveContext,
    ) -> Tuple[List[RecoveryEntry], bytes]:
        """Scan buffer for signatures and resolve pending footer matches."""
        entries: List[RecoveryEntry] = []
        consumed_until = 0

        entries.extend(self._resolve_pending_footers(buffer, base_offset, context))

        active_signatures = FILE_SIGNATURES
        if not context.deep_scan:
            active_signatures = {
                label: signature
                for label, signature in FILE_SIGNATURES.items()
                if label in QUICK_SCAN_SIGNATURES
            }

        for label, signature in active_signatures.items():
            header = signature["header"]
            footer = signature.get("footer")
            max_size = int(signature.get("max_size", 10 * 1024 * 1024))
            extensions = signature.get("extensions", [".bin"])
            extension = extensions[0]
            header_offset = int(signature.get("header_offset", 0))

            search_start = 0
            while True:
                index = buffer.find(header, search_start)
                if index < 0:
                    break

                file_start = index - header_offset
                if file_start < 0:
                    search_start = index + 1
                    continue

                absolute_offset = base_offset + file_start
                if absolute_offset in context.seen_offsets:
                    search_start = index + 1
                    continue

                if footer:
                    end_index = buffer.find(footer, index + len(header))
                    if end_index < 0:
                        context.pending.append(
                            PendingCarve(
                                label=label,
                                signature=signature,
                                absolute_offset=absolute_offset,
                                header_index=file_start,
                                extension=extension,
                            )
                        )
                        search_start = index + 1
                        continue

                    file_data = buffer[file_start : end_index + len(footer)]
                    entry = self._finalize_carve(
                        file_data=file_data,
                        label=label,
                        absolute_offset=absolute_offset,
                        extension=extension,
                        context=context,
                    )
                    if entry:
                        entries.append(entry)
                        consumed_until = max(consumed_until, file_start + len(file_data))
                else:
                    file_data = self._read_file_from_device(
                        absolute_offset=absolute_offset,
                        signature_label=label,
                        max_size=max_size,
                        context=context,
                    )
                    entry = self._finalize_carve(
                        file_data=file_data,
                        label=label,
                        absolute_offset=absolute_offset,
                        extension=extension,
                        context=context,
                    )
                    if entry:
                        entries.append(entry)
                        consumed_until = max(consumed_until, file_start + entry.size_bytes)

                search_start = index + len(header)

        if consumed_until > overlap:
            buffer = buffer[consumed_until - overlap :]
        elif len(buffer) > overlap * 4:
            buffer = buffer[-overlap:]

        return entries, buffer

    def _resolve_pending_footers(
        self,
        buffer: bytes,
        base_offset: int,
        context: CarveContext,
    ) -> List[RecoveryEntry]:
        """Complete pending carves when footers arrive in later buffer chunks."""
        entries: List[RecoveryEntry] = []
        still_pending: List[PendingCarve] = []

        for pending in context.pending:
            if pending.absolute_offset < base_offset:
                continue
            if pending.absolute_offset in context.seen_offsets:
                continue

            relative_start = pending.absolute_offset - base_offset
            header = pending.signature["header"]
            footer = pending.signature.get("footer")
            if footer is None or relative_start < 0 or relative_start >= len(buffer):
                still_pending.append(pending)
                continue

            header_index = buffer.find(header, relative_start)
            if header_index < 0:
                still_pending.append(pending)
                continue

            file_start = header_index - int(pending.signature.get("header_offset", 0))
            if file_start < 0:
                still_pending.append(pending)
                continue

            end_index = buffer.find(footer, header_index + len(header))
            if end_index < 0:
                still_pending.append(pending)
                continue

            file_data = buffer[file_start : end_index + len(footer)]
            entry = self._finalize_carve(
                file_data=file_data,
                label=pending.label,
                absolute_offset=pending.absolute_offset,
                extension=pending.extension,
                context=context,
            )
            if entry:
                entries.append(entry)

        context.pending = still_pending
        return entries

    def _read_file_from_device(
        self,
        absolute_offset: int,
        signature_label: str,
        max_size: int,
        context: CarveContext,
    ) -> bytes:
        """
        Read a footerless file from the device using format-specific size detection.

        Performs an initial read, detects size via format parsers, then reads
        the full extent up to max_size.
        """
        probe_size = min(max_size, 512 * 1024)
        context.device.seek(absolute_offset)
        probe = context.device.read(probe_size)
        if not probe:
            return b""

        detected = detect_file_size(probe, signature_label)
        read_size = min(max_size, detected if detected > 0 else max_size)

        if read_size <= len(probe):
            return probe[:read_size]

        context.device.seek(absolute_offset)
        return context.device.read(read_size)

    def _finalize_carve(
        self,
        file_data: bytes,
        label: str,
        absolute_offset: int,
        extension: str,
        context: CarveContext,
    ) -> Optional[RecoveryEntry]:
        """Validate, deduplicate, write preview, and build a RecoveryEntry."""
        if not file_data or absolute_offset in context.seen_offsets:
            return None

        if not validate_carved_file(file_data, label):
            return None

        context.seen_offsets.add(absolute_offset)

        preview_name = f"carved_{absolute_offset}{extension}"
        preview_path = os.path.join(context.preview_dir, preview_name)
        try:
            with open(preview_path, "wb") as preview_file:
                preview_file.write(file_data)
        except OSError as exc:
            logger.warning("Could not write preview %s: %s", preview_path, exc)
            return None

        entry_id = f"carved_{uuid.uuid4().hex[:12]}"
        confidence = "high" if label in QUICK_SCAN_SIGNATURES else "medium"

        return RecoveryEntry(
            entry_id=entry_id,
            name=preview_name,
            relative_path=f"/carved/{preview_name}",
            entry_type=EntryType.CARVED,
            size_bytes=len(file_data),
            source_target_id=context.source_target_id,
            device_path=context.device_path,
            byte_offset=absolute_offset,
            mime_type=self._guess_mime(extension),
            extension=extension,
            preview_path=preview_path,
            extra={
                "signature": label,
                "absolute_offset": absolute_offset,
                "confidence": confidence,
            },
        )

    @staticmethod
    def _guess_mime(extension: str) -> str:
        """Map common extensions to MIME types for the details panel."""
        mapping: Dict[str, str] = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".tif": "image/tiff",
            ".tiff": "image/tiff",
            ".webp": "image/webp",
            ".pdf": "application/pdf",
            ".rtf": "application/rtf",
            ".zip": "application/zip",
            ".7z": "application/x-7z-compressed",
            ".rar": "application/vnd.rar",
            ".gz": "application/gzip",
            ".doc": "application/msword",
            ".xls": "application/vnd.ms-excel",
            ".ppt": "application/vnd.ms-powerpoint",
            ".db": "application/x-sqlite3",
            ".sqlite": "application/x-sqlite3",
            ".sqlite3": "application/x-sqlite3",
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".oga": "audio/ogg",
            ".mp4": "video/mp4",
            ".m4v": "video/mp4",
            ".avi": "video/x-msvideo",
            ".mkv": "video/x-matroska",
            ".elf": "application/x-elf",
        }
        return mapping.get(extension.lower(), "application/octet-stream")

    def _extract_from_buffer(
        self,
        buffer: bytes,
        device_path: str,
        base_offset: int,
        source_target_id: str,
        overlap: int,
    ) -> Tuple[List[RecoveryEntry], bytes]:
        """
        Legacy buffer extraction used by unit tests.

        Uses an in-memory device image when the path is not readable.
        """
        from io import BytesIO

        device_data = b"\x00" * base_offset + buffer
        device = BytesIO(device_data)
        context = CarveContext(
            device=device,
            device_path=device_path,
            source_target_id=source_target_id,
            preview_dir=self.preview_dir,
            deep_scan=True,
        )
        return self._process_buffer(buffer, base_offset, overlap, context)
