"""
Unit tests for NTFS boot sector parsing.
"""

import pytest

from services.filesystems.ntfs.boot_sector import NtfsBootSector
from tests.ntfs_helpers import create_ntfs_image, tools_available


@pytest.mark.skipif(not tools_available(), reason="mkntfs/ntfscp/ntfsinfo not available")
class TestNtfsBootSector:
    """Tests for NTFS boot sector reading."""

    def test_read_boot_sector_from_image(self, tmp_path):
        """mkntfs image exposes valid boot sector fields."""
        image = tmp_path / "test.ntfs"
        create_ntfs_image(image, size_mb=64)

        with open(image, "rb") as device:
            boot_sector = NtfsBootSector.read_from_device(device)

        assert boot_sector.bytes_per_sector == 512
        assert boot_sector.sectors_per_cluster >= 1
        assert boot_sector.mft_lcn >= 1
        assert boot_sector.mft_record_size in (256, 512, 1024, 2048, 4096)
        assert boot_sector.cluster_size > 0
        assert boot_sector.mft_byte_offset == boot_sector.mft_lcn * boot_sector.cluster_size

    def test_rejects_non_ntfs_data(self, tmp_path):
        """Random bytes are rejected as invalid NTFS."""
        image = tmp_path / "bad.img"
        image.write_bytes(b"\x00" * 4096)

        with open(image, "rb") as device:
            with pytest.raises(ValueError, match="Not an NTFS"):
                NtfsBootSector.read_from_device(device)

    def test_negative_mft_record_size_byte_is_a_power_of_two(self, tmp_path):
        """
        Regression test for the signed-byte MFT record size encoding: a raw
        value of -10 (0xF6) must decode to 1024, not to some cluster-multiple
        misinterpretation of the same byte.
        """
        raw = bytearray(512)
        raw[3:11] = b"NTFS    "
        raw[0x0B:0x0D] = (512).to_bytes(2, "little")
        raw[0x0D] = 8
        raw[0x28:0x30] = (131072).to_bytes(8, "little")
        raw[0x30:0x38] = (4).to_bytes(8, "little")
        raw[0x38:0x40] = (8191).to_bytes(8, "little")
        raw[0x40] = 0xF6  # -10 as a signed byte
        raw[0x1FE:0x200] = b"\x55\xaa"

        image = tmp_path / "custom.img"
        image.write_bytes(bytes(raw))

        with open(image, "rb") as device:
            boot_sector = NtfsBootSector.read_from_device(device)

        assert boot_sector.mft_record_size == 1024
