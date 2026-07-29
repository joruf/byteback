"""
Unit tests for ext4 superblock parsing.
"""

import pytest

from services.filesystems.ext4.superblock import Ext4Superblock
from tests.ext4_helpers import create_ext4_image, dumpe2fs_group_descriptors, tools_available


@pytest.mark.skipif(not tools_available(), reason="mkfs.ext4/debugfs not available")
class TestExt4Superblock:
    """Tests for ext4 superblock reading."""

    def test_read_superblock_from_image(self, tmp_path):
        """mkfs.ext4 image exposes valid superblock fields."""
        image = tmp_path / "test.ext4"
        create_ext4_image(image, size_mb=16)

        with open(image, "rb") as device:
            superblock = Ext4Superblock.read_from_device(device)

        assert superblock.block_size >= 1024
        assert superblock.inode_size in (128, 256)
        assert superblock.inode_count > 0
        assert superblock.group_count >= 1

    def test_rejects_non_ext4_data(self, tmp_path):
        """Random bytes are rejected as invalid ext4."""
        image = tmp_path / "bad.img"
        image.write_bytes(b"\x00" * 4096)

        with open(image, "rb") as device:
            with pytest.raises(ValueError, match="Not an ext4"):
                Ext4Superblock.read_from_device(device)


@pytest.mark.skipif(not tools_available(), reason="mkfs.ext4/debugfs/dumpe2fs not available")
class TestExt4MultiGroupDescriptors:
    """
    Regression tests for group-descriptor size handling.

    A filesystem large enough to span multiple block groups exposes the bug where
    group descriptors were always read at a hardcoded 64-byte stride: with the
    ``64bit`` feature disabled, real descriptors are 32 bytes, so every group
    beyond group 0 used to be parsed from the wrong offset. Expected values are
    cross-checked against ``dumpe2fs``, an independent, trusted parser.
    """

    def test_32byte_descriptors_multi_group(self, tmp_path):
        """Classic 32-byte descriptors (64bit feature disabled) parse correctly beyond group 0."""
        image = tmp_path / "small32.img"
        create_ext4_image(image, size_mb=200, extra_mkfs_args=["-O", "^64bit"])
        expected = dumpe2fs_group_descriptors(image)
        assert len(expected) >= 2, "test image must span at least two block groups"

        with open(image, "rb") as device:
            superblock = Ext4Superblock.read_from_device(device)
            assert superblock.descriptor_size == 32
            assert superblock.group_count == len(expected)
            groups = superblock.load_group_descriptors(device)

        for exp, actual in zip(expected, groups):
            assert actual.block_bitmap_block == exp["block_bitmap_block"]
            assert actual.inode_bitmap_block == exp["inode_bitmap_block"]
            assert actual.inode_table_block == exp["inode_table_block"]
            assert actual.free_blocks == exp["free_blocks"]
            assert actual.free_inodes == exp["free_inodes"]

    def test_64byte_descriptors_multi_group(self, tmp_path):
        """Default 64-byte descriptors (64bit feature enabled) parse correctly beyond group 0."""
        image = tmp_path / "big64.img"
        create_ext4_image(image, size_mb=200)
        expected = dumpe2fs_group_descriptors(image)
        assert len(expected) >= 2, "test image must span at least two block groups"

        with open(image, "rb") as device:
            superblock = Ext4Superblock.read_from_device(device)
            assert superblock.descriptor_size == 64
            assert superblock.group_count == len(expected)
            groups = superblock.load_group_descriptors(device)

        for exp, actual in zip(expected, groups):
            assert actual.block_bitmap_block == exp["block_bitmap_block"]
            assert actual.inode_bitmap_block == exp["inode_bitmap_block"]
            assert actual.inode_table_block == exp["inode_table_block"]
            assert actual.free_blocks == exp["free_blocks"]
            assert actual.free_inodes == exp["free_inodes"]
