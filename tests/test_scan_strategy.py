"""
Tests for scan mode resolution and execution dispatch.
"""

from config.scan_settings import (
    SCAN_MODE_EXT4_DELETED,
    SCAN_MODE_FILESYSTEM,
    SCAN_MODE_FREE_SPACE,
)
from models.storage_target import StorageTarget, TargetType
from services.scanning.scan_strategy import ScanStrategyResolver


class TestScanStrategyResolver:
    """Tests for scan strategy resolution."""

    def _target(self, **kwargs):
        defaults = {
            "target_id": "t1",
            "name": "sda1",
            "device_path": "/dev/sda1",
            "target_type": TargetType.PARTITION,
            "size_bytes": 1024,
        }
        defaults.update(kwargs)
        return StorageTarget(**defaults)

    def test_filesystem_mode_for_mounted_partition(self, tmp_path):
        """Filesystem mode selected for mounted partitions."""
        resolver = ScanStrategyResolver()
        target = self._target(mountpoint=str(tmp_path), filesystem="ext4")

        assert resolver.resolve(target, SCAN_MODE_FILESYSTEM) == ScanStrategyResolver.MODE_FILESYSTEM

    def test_ext4_deleted_mode(self, tmp_path):
        """Explicit ext4 deleted mode is returned."""
        resolver = ScanStrategyResolver()
        target = self._target(filesystem="ext4", mountpoint=str(tmp_path))

        assert resolver.resolve(target, SCAN_MODE_EXT4_DELETED) == ScanStrategyResolver.MODE_EXT4_DELETED

    def test_free_space_mode(self):
        """Explicit free-space mode is returned."""
        resolver = ScanStrategyResolver()
        target = self._target(filesystem="ext4")

        assert resolver.resolve(target, SCAN_MODE_FREE_SPACE) == ScanStrategyResolver.MODE_FREE_SPACE
