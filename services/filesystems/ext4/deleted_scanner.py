"""
Scan ext4 filesystems for deleted inodes and recover file content.
"""

import logging
import os
import tempfile
import uuid
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from config.storage_paths import PREVIEW_DIR_NAME
from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget
from services.filesystems.ext4.inode import Ext4Inode, read_inode_raw
from services.filesystems.ext4.superblock import Ext4Superblock
from utils.device_io import open_device
from utils.file_info import detect_mime_type

logger = logging.getLogger(__name__)


class Ext4DeletedScanner:
    """
    Recover deleted regular files by scanning ext4 inode tables on a raw device.

    Always reads from ``target.device_path`` (never the mountpoint) to access
    on-disk inode metadata directly.
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
        start_inode: int = 0,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[RecoveryEntry], int]:
        """
        Scan all block groups for deleted file inodes.

        Args:
            target: ext4 partition or image target.
            source_target_id: ID stored on each RecoveryEntry.
            start_inode: Resume scanning from this inode number.
            on_entry: Callback for each recovered deleted file.
            on_progress: Callback(processed, total, description).
            should_pause: Returns True when scanning should pause.
            should_cancel: Returns True when scanning should abort.

        Returns:
            Tuple of (entries, final_inode_number).
        """
        entries: List[RecoveryEntry] = []
        device_path = target.device_path

        with open_device(device_path) as device:
            superblock = Ext4Superblock.read_from_device(device)
            total = superblock.inode_count

            for inode_number in range(max(superblock.first_inode, start_inode), total + 1):
                if should_cancel and should_cancel():
                    break

                while should_pause and should_pause():
                    if should_cancel and should_cancel():
                        return entries, inode_number
                    import time

                    time.sleep(0.2)

                if on_progress:
                    on_progress(inode_number, total, f"Inode {inode_number}")

                try:
                    raw_inode = read_inode_raw(device, superblock, inode_number)
                    inode = Ext4Inode.parse(inode_number, raw_inode)
                except (OSError, ValueError):
                    continue

                if not inode.is_deleted or not inode.is_regular_file:
                    continue

                if inode.size <= 0:
                    continue

                try:
                    data = inode.read_file_data(device, superblock, raw_inode)
                except OSError as exc:
                    logger.debug("Could not read inode %s data: %s", inode_number, exc)
                    continue

                if not data:
                    continue

                entry = self._build_entry(
                    inode=inode,
                    data=data,
                    device_path=device_path,
                    source_target_id=source_target_id,
                )
                if entry:
                    entries.append(entry)
                    if on_entry:
                        on_entry(entry)

        return entries, total

    def _build_entry(
        self,
        inode: Ext4Inode,
        data: bytes,
        device_path: str,
        source_target_id: str,
    ) -> Optional[RecoveryEntry]:
        """Write preview data and build a RecoveryEntry."""
        extension = self._guess_extension(data)
        preview_name = f"deleted_inode_{inode.inode_number}{extension}"
        preview_path = os.path.join(self.preview_dir, preview_name)

        try:
            with open(preview_path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            logger.warning("Could not write deleted inode preview: %s", exc)
            return None

        deleted_at = (
            datetime.fromtimestamp(inode.deletion_time).strftime("%Y-%m-%d %H:%M:%S")
            if inode.deletion_time > 0
            else None
        )

        return RecoveryEntry(
            entry_id=f"deleted_{uuid.uuid4().hex[:12]}",
            name=preview_name,
            relative_path=f"/deleted/{preview_name}",
            entry_type=EntryType.DELETED,
            size_bytes=len(data),
            source_target_id=source_target_id,
            device_path=device_path,
            byte_offset=0,
            mime_type=detect_mime_type(preview_path),
            extension=extension,
            modified_time=deleted_at,
            preview_path=preview_path,
            extra={
                "inode_number": inode.inode_number,
                "deletion_time": inode.deletion_time,
                "recovery_method": "ext4_deleted_inode",
                "confidence": "high",
            },
        )

    @staticmethod
    def _guess_extension(data: bytes) -> str:
        """Guess file extension from magic bytes."""
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith(b"\x89PNG"):
            return ".png"
        if data.startswith(b"%PDF"):
            return ".pdf"
        if data.startswith(b"PK\x03\x04"):
            return ".zip"
        if data.startswith(b"GIF8"):
            return ".gif"
        if data.startswith(b"\x7fELF"):
            return ".elf"
        if data.startswith(b"SQLite format 3"):
            return ".sqlite"
        return ".bin"

    @staticmethod
    def supports_target(target: StorageTarget) -> bool:
        """
        Return True when the target appears to be an ext4 volume.

        Args:
            target: Storage target to inspect.

        Returns:
            True for ext4 partitions, images, or unallocated ext4 regions.
        """
        if target.filesystem and "ext" in target.filesystem.lower():
            return True
        try:
            with open_device(target.device_path) as device:
                Ext4Superblock.read_from_device(device)
            return True
        except (OSError, ValueError):
            return False
