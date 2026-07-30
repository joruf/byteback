"""
Scan NTFS filesystems for deleted files and recover their content.
"""

import logging
import os
import tempfile
import uuid
from typing import Callable, List, Optional, Tuple

from config.storage_paths import PREVIEW_DIR_NAME
from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget
from services.filesystems.ntfs.binary import ATTR_DATA, read_exact
from services.filesystems.ntfs.boot_sector import NtfsBootSector
from services.filesystems.ntfs.data_run import resolve_runs_to_clusters, read_virtual_range
from services.filesystems.ntfs.mft_record import MftRecord
from utils.device_io import open_device
from utils.file_info import detect_mime_type

logger = logging.getLogger(__name__)


class NtfsDeletedScanner:
    """
    Recover deleted files by scanning NTFS MFT records on a raw device.

    Always reads from ``target.device_path`` (never the mountpoint) to access
    on-disk MFT metadata directly. Deleting a file only clears the InUse flag
    in its MFT record (see ``MftRecord.is_deleted``) — the record's
    $FILE_NAME and $DATA attributes normally stay intact until that record
    slot is reused for a new file, so both the original filename and content
    are recoverable, the same way ``Ext4DeletedScanner`` recovers from
    ext4 inodes (MFT records are NTFS's direct equivalent).
    """

    def __init__(self, preview_dir: Optional[str] = None) -> None:
        """
        Args:
            preview_dir: Directory for recovered preview files.
        """
        self.preview_dir = preview_dir or os.path.join(
            tempfile.gettempdir(),
            PREVIEW_DIR_NAME,
        )
        os.makedirs(self.preview_dir, exist_ok=True)

    def scan(
        self,
        target: StorageTarget,
        source_target_id: str,
        start_record: int = 0,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[RecoveryEntry], int]:
        """
        Scan all MFT records for deleted file entries.

        Args:
            target: NTFS partition or image target.
            source_target_id: ID stored on each RecoveryEntry.
            start_record: Resume scanning from this MFT record number.
            on_entry: Callback for each recovered deleted file.
            on_progress: Callback(record_number, total_records, description).
            should_pause: Returns True when scanning should pause.
            should_cancel: Returns True when scanning should abort.

        Returns:
            Tuple of (entries, final MFT record number reached).
        """
        entries: List[RecoveryEntry] = []
        device_path = target.device_path

        with open_device(device_path) as device:
            boot_sector = NtfsBootSector.read_from_device(device)
            mft_clusters, total_records = self._resolve_mft_layout(device, boot_sector)
            final_record = total_records

            for record_number in range(max(start_record, 0), total_records):
                if should_cancel and should_cancel():
                    # Record the actual stopping point, not the total, so a
                    # resumed scan continues from here instead of believing
                    # the whole MFT was already covered.
                    final_record = record_number
                    break

                if should_pause and should_pause():
                    # Return immediately (rather than blocking this thread in
                    # a sleep loop) so the worker can persist a resumable
                    # checkpoint and let the thread exit; resuming starts a
                    # fresh call at this record.
                    return entries, record_number

                if on_progress:
                    on_progress(record_number, total_records, f"MFT record {record_number}")

                try:
                    raw = read_virtual_range(
                        device,
                        mft_clusters,
                        boot_sector.cluster_size,
                        record_number * boot_sector.mft_record_size,
                        boot_sector.mft_record_size,
                    )
                    record = MftRecord.parse(record_number, raw)
                except OSError as exc:
                    logger.debug("Could not read MFT record %s: %s", record_number, exc)
                    continue

                if record is None or record.is_in_use or record.is_directory:
                    continue

                name = record.get_file_name()
                if not name:
                    continue

                try:
                    data = record.read_data(device, boot_sector)
                except OSError as exc:
                    logger.debug("Could not read data for MFT record %s: %s", record_number, exc)
                    continue
                if not data:
                    continue

                entry = self._build_entry(
                    record=record,
                    name=name,
                    data=data,
                    device_path=device_path,
                    source_target_id=source_target_id,
                )
                if entry:
                    entries.append(entry)
                    if on_entry:
                        on_entry(entry)

        return entries, final_record

    @staticmethod
    def _resolve_mft_layout(device, boot_sector: NtfsBootSector):
        """
        Bootstrap the $MFT's own cluster layout from MFT record 0.

        Returns:
            Tuple of (flat per-cluster LCN list for the whole $MFT stream,
            total MFT record count).
        """
        raw_bootstrap = read_exact(device, boot_sector.mft_byte_offset, boot_sector.mft_record_size)
        record_zero = MftRecord.parse(0, raw_bootstrap)
        if record_zero is None:
            raise ValueError("Could not parse MFT record 0 ($MFT itself)")

        data_attr = record_zero.get_attribute(ATTR_DATA)
        if data_attr is None or not data_attr.is_non_resident:
            raise ValueError("$MFT's $DATA attribute is missing or unexpectedly resident")

        clusters = resolve_runs_to_clusters(data_attr.data_runs)
        total_records = data_attr.real_size // boot_sector.mft_record_size
        return clusters, total_records

    def _build_entry(
        self,
        record: MftRecord,
        name: str,
        data: bytes,
        device_path: str,
        source_target_id: str,
    ) -> Optional[RecoveryEntry]:
        """Write preview data and build a RecoveryEntry."""
        extension = os.path.splitext(name)[1].lower() or ".bin"
        preview_name = f"deleted_{uuid.uuid4().hex[:8]}_{name}"
        preview_path = os.path.join(self.preview_dir, preview_name)

        try:
            with open(preview_path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            logger.warning("Could not write deleted file preview: %s", exc)
            return None

        return RecoveryEntry(
            entry_id=f"deleted_{uuid.uuid4().hex[:12]}",
            name=name,
            relative_path=f"/deleted/{preview_name}",
            entry_type=EntryType.DELETED,
            size_bytes=len(data),
            source_target_id=source_target_id,
            device_path=device_path,
            byte_offset=0,
            mime_type=detect_mime_type(preview_path),
            extension=extension,
            preview_path=preview_path,
            extra={
                "original_name": name,
                "mft_record_number": record.record_number,
                "recovery_method": "ntfs_deleted_mft_record",
                "confidence": "medium",
            },
        )

    @staticmethod
    def supports_target(target: StorageTarget) -> bool:
        """
        Return True when the target appears to be a readable NTFS volume.

        Args:
            target: Storage target to inspect.

        Returns:
            True for NTFS partitions, images, or unallocated NTFS regions.
        """
        try:
            with open_device(target.device_path) as device:
                NtfsBootSector.read_from_device(device)
            return True
        except (OSError, ValueError):
            return False
