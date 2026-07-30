"""
Unit tests for FAT32 boot sector parsing and cluster-chain walking.
"""

import pytest

from services.filesystems.fat32.boot_sector import Fat32BootSector
from tests.fat32_helpers import create_fat32_image, tools_available


@pytest.mark.skipif(not tools_available(), reason="mkfs.vfat/mtools not available")
class TestFat32BootSector:
    """Tests for FAT32 boot sector reading."""

    def test_read_boot_sector_from_image(self, tmp_path):
        """mkfs.vfat image exposes valid BPB fields."""
        image = tmp_path / "test.fat32"
        create_fat32_image(image, size_mb=64)

        with open(image, "rb") as device:
            boot_sector = Fat32BootSector.read_from_device(device)

        assert boot_sector.bytes_per_sector == 512
        assert boot_sector.sectors_per_cluster >= 1
        assert boot_sector.num_fats == 2
        assert boot_sector.root_cluster >= 2
        assert boot_sector.cluster_size > 0

    def test_rejects_non_fat_data(self, tmp_path):
        """Random bytes are rejected as invalid FAT."""
        image = tmp_path / "bad.img"
        image.write_bytes(b"\x00" * 4096)

        with open(image, "rb") as device:
            with pytest.raises(ValueError, match="Not a FAT"):
                Fat32BootSector.read_from_device(device)

    def test_rejects_fat16_bpb_shape(self, tmp_path):
        """A FAT16-shaped BPB (root_entry_count/fat_size_16 set) is rejected as non-FAT32."""
        raw = bytearray(512)
        raw[0x0B:0x0D] = (512).to_bytes(2, "little")
        raw[0x0D] = 4
        raw[0x0E:0x10] = (4).to_bytes(2, "little")
        raw[0x10] = 2
        raw[0x11:0x13] = (512).to_bytes(2, "little")  # FAT16-style fixed root dir
        raw[0x16:0x18] = (32).to_bytes(2, "little")  # FAT16-style 16-bit FAT size
        raw[0x1FE:0x200] = b"\x55\xaa"

        image = tmp_path / "fat16like.img"
        image.write_bytes(bytes(raw))

        with open(image, "rb") as device:
            with pytest.raises(ValueError, match="FAT12/FAT16"):
                Fat32BootSector.read_from_device(device)
