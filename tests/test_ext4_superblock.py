"""
Unit tests for ext4 superblock parsing.
"""

import pytest

from services.filesystems.ext4.superblock import Ext4Superblock
from tests.ext4_helpers import create_ext4_image, tools_available


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
