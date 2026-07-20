"""
Dispatches scan execution to the appropriate recovery backend.
"""

import logging
from typing import Callable, List, Optional, Tuple

from config.scan_settings import CARVE_SCAN_INTERVAL, FILESYSTEM_SCAN_INTERVAL
from models.recovery_entry import RecoveryEntry
from models.storage_target import StorageTarget
from services.carving.file_carver import FileCarver
from services.filesystems.ext4.deleted_scanner import Ext4DeletedScanner
from services.filesystems.ext4.free_space import Ext4FreeSpaceScanner
from services.scanning.filesystem_scanner import FilesystemScanner
from services.scanning.scan_strategy import ScanStrategyResolver

logger = logging.getLogger(__name__)


class ScanExecutor:
    """
    Runs one scan pass using the scanner appropriate for the resolved mode.
    """

    def __init__(self) -> None:
        """Initialize scanner backends."""
        self._filesystem_scanner = FilesystemScanner()
        self._file_carver = FileCarver()
        self._deleted_scanner = Ext4DeletedScanner()
        self._free_space_scanner = Ext4FreeSpaceScanner()
        self._strategy = ScanStrategyResolver()

    def execute(
        self,
        target: StorageTarget,
        scan_strategy: str,
        source_entries: List[RecoveryEntry],
        filesystem_queue: Optional[List[str]],
        carve_offset: int,
        ext4_inode_cursor: int,
        free_space_range_index: int,
        on_entry: Callable[[RecoveryEntry], None],
        on_progress: Callable[[int, int, str], None],
        should_pause: Callable[[], bool],
        should_cancel: Callable[[], bool],
    ) -> Tuple[str, List[RecoveryEntry], dict]:
        """
        Execute a scan and return updated checkpoint state.

        Returns:
            Tuple of (internal_mode, entries, checkpoint_dict).
        """
        mode = self._strategy.resolve(target, scan_strategy)

        if mode == ScanStrategyResolver.MODE_FILESYSTEM:
            entries, queue, processed = self._filesystem_scanner.scan(
                target=target,
                source_target_id=target.target_id,
                initial_queue=filesystem_queue or None,
                known_entries={entry.entry_id: entry for entry in source_entries},
                on_entry=on_entry,
                on_progress=on_progress,
                should_pause=should_pause,
                should_cancel=should_cancel,
            )
            return mode, entries, {
                "bytes_processed": processed,
                "filesystem_queue": queue,
                "carve_offset": carve_offset,
                "ext4_inode_cursor": ext4_inode_cursor,
                "free_space_range_index": free_space_range_index,
            }

        if mode == ScanStrategyResolver.MODE_EXT4_DELETED:
            if not Ext4DeletedScanner.supports_target(target):
                raise ValueError("Selected target is not a readable ext4 filesystem")

            entries, final_inode = self._deleted_scanner.scan(
                target=target,
                source_target_id=target.target_id,
                start_inode=ext4_inode_cursor,
                on_entry=on_entry,
                on_progress=on_progress,
                should_pause=should_pause,
                should_cancel=should_cancel,
            )
            merged = list(source_entries) + entries
            return mode, merged, {
                "bytes_processed": final_inode,
                "filesystem_queue": filesystem_queue or [],
                "carve_offset": carve_offset,
                "ext4_inode_cursor": final_inode,
                "free_space_range_index": free_space_range_index,
            }

        if mode == ScanStrategyResolver.MODE_FREE_SPACE:
            if not Ext4FreeSpaceScanner.supports_target(target):
                raise ValueError("Selected target is not a readable ext4 filesystem")

            deep_scan = scan_strategy != "quick_carve"
            entries, processed, final_range = self._free_space_scanner.scan(
                target=target,
                source_target_id=target.target_id,
                start_range_index=free_space_range_index,
                deep_scan=deep_scan,
                on_entry=on_entry,
                on_progress=on_progress,
                should_pause=should_pause,
                should_cancel=should_cancel,
            )
            merged = list(source_entries) + entries
            return mode, merged, {
                "bytes_processed": processed,
                "filesystem_queue": filesystem_queue or [],
                "carve_offset": carve_offset,
                "ext4_inode_cursor": ext4_inode_cursor,
                "free_space_range_index": final_range,
            }

        deep_scan = mode == ScanStrategyResolver.MODE_DEEP_CARVE
        entries, final_offset = self._file_carver.carve_range(
            target=target,
            start_offset=target.start_offset,
            size_bytes=target.size_bytes,
            source_target_id=target.target_id,
            start_from=carve_offset,
            deep_scan=deep_scan,
            on_entry=on_entry,
            on_progress=on_progress,
            should_pause=should_pause,
            should_cancel=should_cancel,
        )
        merged = list(source_entries) + entries
        return mode, merged, {
            "bytes_processed": final_offset,
            "filesystem_queue": filesystem_queue or [],
            "carve_offset": final_offset,
            "ext4_inode_cursor": ext4_inode_cursor,
            "free_space_range_index": free_space_range_index,
        }

    @staticmethod
    def progress_interval_for_mode(mode: str) -> float:
        """Return the recommended progress throttle interval for a mode."""
        if mode == ScanStrategyResolver.MODE_FILESYSTEM:
            return FILESYSTEM_SCAN_INTERVAL
        return CARVE_SCAN_INTERVAL
