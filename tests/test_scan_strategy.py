"""
Tests for scan mode resolution and execution dispatch.
"""

import pytest

from config.scan_settings import (
    SCAN_MODE_AUTO,
    SCAN_MODE_EXFAT_DELETED,
    SCAN_MODE_EXT4_DELETED,
    SCAN_MODE_FAT32_DELETED,
    SCAN_MODE_FILESYSTEM,
    SCAN_MODE_FREE_SPACE,
    SCAN_MODE_NTFS_DELETED,
)
from models.storage_target import StorageTarget, TargetType
from services.scanning.scan_strategy import ScanStrategyResolver
from tests.exfat_helpers import create_exfat_image, tools_available as exfat_tools_available
from tests.fat32_helpers import create_fat32_image, tools_available
from tests.ntfs_helpers import create_ntfs_image, tools_available as ntfs_tools_available


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

    def test_fat32_deleted_mode(self):
        """Explicit FAT32 deleted mode is returned."""
        resolver = ScanStrategyResolver()
        target = self._target(filesystem="vfat")

        assert resolver.resolve(target, SCAN_MODE_FAT32_DELETED) == ScanStrategyResolver.MODE_FAT32_DELETED

    @pytest.mark.skipif(not tools_available(), reason="mkfs.vfat/mtools not available")
    def test_auto_mode_detects_fat32_when_unmounted(self, tmp_path):
        """Auto mode falls through ext4-detection and correctly identifies a FAT32 image."""
        image = tmp_path / "vol.fat32"
        create_fat32_image(image, size_mb=64)
        resolver = ScanStrategyResolver()
        target = self._target(
            device_path=str(image),
            target_type=TargetType.IMAGE,
            filesystem="vfat",
        )

        assert resolver.resolve(target, SCAN_MODE_AUTO) == ScanStrategyResolver.MODE_FAT32_DELETED

    def test_ntfs_deleted_mode(self):
        """Explicit NTFS deleted mode is returned."""
        resolver = ScanStrategyResolver()
        target = self._target(filesystem="ntfs")

        assert resolver.resolve(target, SCAN_MODE_NTFS_DELETED) == ScanStrategyResolver.MODE_NTFS_DELETED

    @pytest.mark.skipif(not ntfs_tools_available(), reason="mkntfs/ntfscp/ntfsinfo not available")
    def test_auto_mode_detects_ntfs_when_unmounted(self, tmp_path):
        """Auto mode falls through ext4/FAT32 detection and correctly identifies an NTFS image."""
        image = tmp_path / "vol.ntfs"
        create_ntfs_image(image, size_mb=64)
        resolver = ScanStrategyResolver()
        target = self._target(
            device_path=str(image),
            target_type=TargetType.IMAGE,
            filesystem="ntfs",
        )

        assert resolver.resolve(target, SCAN_MODE_AUTO) == ScanStrategyResolver.MODE_NTFS_DELETED

    def test_exfat_deleted_mode(self):
        """Explicit exFAT deleted mode is returned."""
        resolver = ScanStrategyResolver()
        target = self._target(filesystem="exfat")

        assert resolver.resolve(target, SCAN_MODE_EXFAT_DELETED) == ScanStrategyResolver.MODE_EXFAT_DELETED

    @pytest.mark.skipif(not exfat_tools_available(), reason="mkfs.exfat not available")
    def test_auto_mode_detects_exfat_when_unmounted(self, tmp_path):
        """Auto mode falls through ext4/FAT32/NTFS detection and correctly identifies an exFAT image."""
        image = tmp_path / "vol.exfat"
        create_exfat_image(image, size_mb=8)
        resolver = ScanStrategyResolver()
        target = self._target(
            device_path=str(image),
            target_type=TargetType.IMAGE,
            filesystem="exfat",
        )

        assert resolver.resolve(target, SCAN_MODE_AUTO) == ScanStrategyResolver.MODE_EXFAT_DELETED
