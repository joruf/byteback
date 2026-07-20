"""
Scan mode resolution for the background worker.
"""

import os

from config.scan_settings import (
    SCAN_MODE_AUTO,
    SCAN_MODE_DEEP_CARVE,
    SCAN_MODE_EXT4_DELETED,
    SCAN_MODE_FILESYSTEM,
    SCAN_MODE_FREE_SPACE,
    SCAN_MODE_QUICK_CARVE,
)
from models.storage_target import StorageTarget, TargetType
from services.filesystems.ext4.deleted_scanner import Ext4DeletedScanner


class ScanStrategyResolver:
    """Maps user-selected scan modes to internal execution modes."""

    MODE_FILESYSTEM = "filesystem"
    MODE_DEEP_CARVE = "deep_carve"
    MODE_QUICK_CARVE = "quick_carve"
    MODE_EXT4_DELETED = "ext4_deleted"
    MODE_FREE_SPACE = "free_space"

    def resolve(self, target: StorageTarget, scan_strategy: str) -> str:
        """
        Resolve the effective internal scan mode.

        Args:
            target: Selected storage target.
            scan_strategy: User-selected scan strategy constant.

        Returns:
            Internal mode string used by ScanExecutor.
        """
        if scan_strategy == SCAN_MODE_FILESYSTEM:
            return self.MODE_FILESYSTEM if self._can_filesystem_scan(target) else self.MODE_DEEP_CARVE

        if scan_strategy == SCAN_MODE_DEEP_CARVE:
            return self.MODE_DEEP_CARVE

        if scan_strategy == SCAN_MODE_QUICK_CARVE:
            return self.MODE_QUICK_CARVE

        if scan_strategy == SCAN_MODE_EXT4_DELETED:
            return self.MODE_EXT4_DELETED

        if scan_strategy == SCAN_MODE_FREE_SPACE:
            return self.MODE_FREE_SPACE

        if scan_strategy == SCAN_MODE_AUTO:
            if self._can_filesystem_scan(target):
                return self.MODE_FILESYSTEM
            if Ext4DeletedScanner.supports_target(target):
                return self.MODE_EXT4_DELETED
            return self.MODE_DEEP_CARVE

        return self.MODE_DEEP_CARVE

    @staticmethod
    def _can_filesystem_scan(target: StorageTarget) -> bool:
        """True when a live filesystem inventory is possible."""
        if target.target_type != TargetType.PARTITION:
            return False
        if not target.mountpoint:
            return False
        return os.path.isdir(target.mountpoint)
