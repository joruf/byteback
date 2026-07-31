"""
Unit tests for exFAT boot sector parsing and cluster-chain walking.
"""

import struct

import pytest

from services.filesystems.exfat.boot_sector import ExfatBootSector
from tests.exfat_helpers import create_exfat_image, tools_available


@pytest.mark.skipif(not tools_available(), reason="mkfs.exfat not available")
class TestExfatBootSector:
    """Tests for exFAT boot sector reading."""

    def test_read_boot_sector_from_image(self, tmp_path):
        """mkfs.exfat image exposes valid boot sector fields."""
        image = tmp_path / "test.exfat"
        create_exfat_image(image, size_mb=8)

        with open(image, "rb") as device:
            boot_sector = ExfatBootSector.read_from_device(device)

        assert boot_sector.bytes_per_sector == 512
        assert boot_sector.sectors_per_cluster >= 1
        assert boot_sector.root_cluster >= 2
        assert boot_sector.cluster_count > 0
        assert boot_sector.cluster_size > 0

    def test_rejects_non_exfat_data(self, tmp_path):
        """Random bytes are rejected as invalid exFAT."""
        image = tmp_path / "bad.img"
        image.write_bytes(b"\x00" * 4096)

        with open(image, "rb") as device:
            with pytest.raises(ValueError, match="Not an exFAT"):
                ExfatBootSector.read_from_device(device)

    def test_rejects_ntfs_oem_id(self, tmp_path):
        """An NTFS-shaped OEM id (not 'EXFAT   ') is rejected even with a valid boot signature."""
        raw = bytearray(512)
        raw[3:11] = b"NTFS    "
        raw[0x1FE:0x200] = b"\x55\xaa"

        image = tmp_path / "ntfslike.img"
        image.write_bytes(bytes(raw))

        with open(image, "rb") as device:
            with pytest.raises(ValueError, match="Not an exFAT"):
                ExfatBootSector.read_from_device(device)

    def test_shift_encoded_sizes_and_cluster_offset_arithmetic(self, tmp_path):
        """
        Regression test for the shift-encoded sector/cluster sizes: exFAT stores
        BytesPerSectorShift/SectorsPerClusterShift (powers of two), not raw byte
        counts — a shift of 9/3 must decode to 512-byte sectors in 4096-byte
        clusters, and cluster_to_byte_offset must honor the cluster-heap offset.
        """
        raw = bytearray(512)
        raw[3:11] = b"EXFAT   "
        struct.pack_into("<I", raw, 80, 2048)  # FatOffset (sectors)
        struct.pack_into("<I", raw, 88, 4096)  # ClusterHeapOffset (sectors)
        struct.pack_into("<I", raw, 92, 1536)  # ClusterCount
        struct.pack_into("<I", raw, 96, 5)  # FirstClusterOfRootDirectory
        raw[108] = 9  # BytesPerSectorShift -> 512
        raw[109] = 3  # SectorsPerClusterShift -> 8 sectors/cluster
        raw[0x1FE:0x200] = b"\x55\xaa"

        image = tmp_path / "custom.img"
        image.write_bytes(bytes(raw))

        with open(image, "rb") as device:
            boot_sector = ExfatBootSector.read_from_device(device)

        assert boot_sector.bytes_per_sector == 512
        assert boot_sector.sectors_per_cluster == 8
        assert boot_sector.cluster_size == 4096
        assert boot_sector.fat_start_byte == 2048 * 512
        assert boot_sector.cluster_to_byte_offset(2) == 4096 * 512
        assert boot_sector.cluster_to_byte_offset(5) == 4096 * 512 + 3 * 4096
