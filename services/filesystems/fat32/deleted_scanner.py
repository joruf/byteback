"""
Scan FAT32 filesystems for deleted files and recover their content.
"""

import logging
import os
import tempfile
import uuid
from collections import deque
from typing import Callable, List, Optional, Tuple

from config.storage_paths import PREVIEW_DIR_NAME
from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget
from services.filesystems.fat32.binary import read_exact
from services.filesystems.fat32.boot_sector import Fat32BootSector
from services.filesystems.fat32.directory_entry import Fat32DirEntry, list_directory_entries
from utils.device_io import open_device
from utils.file_info import detect_mime_type

logger = logging.getLogger(__name__)


class Fat32DeletedScanner:
    """
    Recover deleted files by walking FAT32 directory entries on a raw device.

    Always reads from ``target.device_path`` (never the mountpoint) to access
    on-disk directory/FAT metadata directly. Directory entries survive
    deletion (only their first name byte is overwritten), so the original
    8.3 filename, size, and starting cluster are recoverable; the FAT chain
    itself is often cleared on delete, so content recovery tries the chain
    first and falls back to assuming contiguous clusters (the same heuristic
    tools like PhotoRec/TestDisk use for FAT).
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
        initial_queue: Optional[List[int]] = None,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[RecoveryEntry], List[int], int]:
        """
        Walk all directories (starting at the root) for deleted file entries.

        Args:
            target: FAT32 partition or image target.
            source_target_id: ID stored on each RecoveryEntry.
            initial_queue: Remaining directory cluster numbers when resuming.
            on_entry: Callback for each recovered deleted file.
            on_progress: Callback(directories_done, directories_total_estimate, description).
            should_pause: Returns True when scanning should pause.
            should_cancel: Returns True when scanning should abort.

        Returns:
            Tuple of (entries, remaining directory-cluster queue, directories processed).
        """
        entries: List[RecoveryEntry] = []
        device_path = target.device_path

        with open_device(device_path) as device:
            boot_sector = Fat32BootSector.read_from_device(device)
            dir_queue: deque = deque(initial_queue or [boot_sector.root_cluster])
            visited = set()
            processed_dirs = 0

            while dir_queue:
                if should_cancel and should_cancel():
                    break

                if should_pause and should_pause():
                    # Return immediately (rather than blocking this thread in a
                    # sleep loop) so the worker can persist a resumable checkpoint
                    # and let the thread exit; resuming starts a fresh call with
                    # this same directory queue.
                    return entries, list(dir_queue), processed_dirs

                cluster = dir_queue.popleft()
                if cluster in visited:
                    continue
                visited.add(cluster)
                processed_dirs += 1

                if on_progress:
                    on_progress(
                        processed_dirs,
                        processed_dirs + len(dir_queue),
                        f"Directory cluster {cluster}",
                    )

                try:
                    dir_entries = list_directory_entries(boot_sector, device, cluster)
                except OSError as exc:
                    logger.debug("Could not read directory at cluster %s: %s", cluster, exc)
                    continue

                for dir_entry in dir_entries:
                    if dir_entry.is_long_name_part or dir_entry.is_volume_label or dir_entry.is_dot_entry:
                        continue

                    if dir_entry.is_directory:
                        if not dir_entry.is_deleted and dir_entry.first_cluster >= 2:
                            dir_queue.append(dir_entry.first_cluster)
                        continue

                    if not dir_entry.is_deleted or dir_entry.file_size <= 0:
                        continue

                    try:
                        data = self._recover_file_data(boot_sector, device, dir_entry)
                    except OSError as exc:
                        logger.debug(
                            "Could not read deleted file %s: %s", dir_entry.name, exc
                        )
                        continue
                    if not data:
                        continue

                    entry = self._build_entry(
                        dir_entry=dir_entry,
                        data=data,
                        device_path=device_path,
                        source_target_id=source_target_id,
                    )
                    if entry:
                        entries.append(entry)
                        if on_entry:
                            on_entry(entry)

        return entries, list(dir_queue), processed_dirs

    @staticmethod
    def _recover_file_data(
        boot_sector: Fat32BootSector,
        device,
        dir_entry: Fat32DirEntry,
    ) -> bytes:
        """
        Recover a deleted file's content.

        Tries the FAT chain from the entry's starting cluster first (intact in
        some deletion scenarios); if it doesn't cover the whole recorded file
        size, falls back to assuming the file was stored in contiguous
        clusters starting there — the same heuristic PhotoRec/TestDisk use,
        since the directory entry's size and starting cluster both survive
        deletion but the FAT chain frequently does not.
        """
        cluster_size = boot_sector.cluster_size
        needed_clusters = -(-dir_entry.file_size // cluster_size)  # ceil division

        chain = (
            boot_sector.walk_cluster_chain(device, dir_entry.first_cluster)
            if dir_entry.first_cluster >= 2
            else []
        )
        if len(chain) < needed_clusters:
            start = dir_entry.first_cluster if dir_entry.first_cluster >= 2 else 2
            chain = list(range(start, start + needed_clusters))

        chunks: List[bytes] = []
        remaining = dir_entry.file_size
        for cluster in chain:
            if remaining <= 0:
                break
            try:
                data = read_exact(device, boot_sector.cluster_to_byte_offset(cluster), cluster_size)
            except OSError:
                break
            take = min(len(data), remaining)
            chunks.append(data[:take])
            remaining -= take

        return b"".join(chunks)

    def _build_entry(
        self,
        dir_entry: Fat32DirEntry,
        data: bytes,
        device_path: str,
        source_target_id: str,
    ) -> Optional[RecoveryEntry]:
        """Write preview data and build a RecoveryEntry."""
        extension = os.path.splitext(dir_entry.name)[1].lower() or ".bin"
        preview_name = f"deleted_{uuid.uuid4().hex[:8]}_{dir_entry.name}"
        preview_path = os.path.join(self.preview_dir, preview_name)

        try:
            with open(preview_path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            logger.warning("Could not write deleted file preview: %s", exc)
            return None

        return RecoveryEntry(
            entry_id=f"deleted_{uuid.uuid4().hex[:12]}",
            name=dir_entry.name,
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
                "original_name": dir_entry.name,
                "first_cluster": dir_entry.first_cluster,
                "recovery_method": "fat32_deleted_entry",
                "confidence": "medium",
            },
        )

    @staticmethod
    def supports_target(target: StorageTarget) -> bool:
        """
        Return True when the target appears to be a readable FAT32 volume.

        Unlike the ext4 scanners, a reported ``filesystem`` of "vfat" is not
        trusted on its own — ``lsblk`` reports FAT12/FAT16/FAT32 identically,
        and only FAT32 has the boot-sector layout this scanner understands
        (no fixed root directory region, a ``root_cluster`` BPB field). The
        boot sector itself distinguishes them, so it is always parsed to
        confirm this is genuinely FAT32.

        Args:
            target: Storage target to inspect.

        Returns:
            True for FAT32 partitions, images, or unallocated FAT32 regions.
        """
        try:
            with open_device(target.device_path) as device:
                Fat32BootSector.read_from_device(device)
            return True
        except (OSError, ValueError):
            return False
