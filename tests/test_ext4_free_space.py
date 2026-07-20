"""
Tests for ext4 free-space block mapping and carving.
"""

import pytest

from models.storage_target import StorageTarget, TargetType
from services.filesystems.ext4.free_space import Ext4FreeSpaceScanner
from services.filesystems.ext4.superblock import Ext4Superblock
from tests.ext4_helpers import create_ext4_image, tools_available


@pytest.mark.skipif(not tools_available(), reason="mkfs.ext4/debugfs not available")
class TestExt4FreeSpaceScanner:
    """Tests for free-space-only carving."""

    def test_collects_free_block_ranges(self, tmp_path):
        """Fresh ext4 image reports free block ranges."""
        image = tmp_path / "free.ext4"
        create_ext4_image(image, size_mb=16)

        with open(image, "rb") as device:
            superblock = Ext4Superblock.read_from_device(device)
            scanner = Ext4FreeSpaceScanner()
            ranges = scanner._collect_free_block_ranges(device, superblock)

        assert len(ranges) >= 1
        total_free_blocks = sum(count for _start, count in ranges)
        assert total_free_blocks > 0

    def test_free_space_scan_runs_on_ext4_image(self, tmp_path):
        """Free-space scan completes without error on ext4 image."""
        image = tmp_path / "free2.ext4"
        create_ext4_image(image, size_mb=16)
        target = StorageTarget(
            target_id="free_test",
            name="free2.ext4",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ext4",
        )

        scanner = Ext4FreeSpaceScanner()
        entries, processed, final_range = scanner.scan(
            target=target,
            source_target_id="free_test",
            deep_scan=False,
        )

        assert processed >= 0
        assert final_range >= 0
        assert isinstance(entries, list)
