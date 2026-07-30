"""
NTFS boot sector parsing.
"""

import struct
from dataclasses import dataclass
from typing import BinaryIO

from services.filesystems.ntfs.binary import (
    NTFS_BOOT_SIGNATURE,
    NTFS_BOOT_SIGNATURE_OFFSET,
    NTFS_OEM_ID,
    read_exact,
    read_le16,
    read_le64,
)


@dataclass
class NtfsBootSector:
    """
    Parsed NTFS boot sector fields required for recovery scanning.

    Attributes:
        bytes_per_sector: Sector size in bytes (almost always 512).
        sectors_per_cluster: Cluster size in sectors.
        total_sectors: Total sectors in the volume.
        mft_lcn: Logical cluster number of the start of the $MFT.
        mft_mirr_lcn: Logical cluster number of the start of the $MFTMirr.
        mft_record_size: Size in bytes of one MFT record (e.g. 1024).
    """

    bytes_per_sector: int
    sectors_per_cluster: int
    total_sectors: int
    mft_lcn: int
    mft_mirr_lcn: int
    mft_record_size: int

    @classmethod
    def read_from_device(cls, device: BinaryIO) -> "NtfsBootSector":
        """
        Parse the boot sector from a block device or image file.

        Args:
            device: Open binary handle positioned at the start of the volume.

        Returns:
            Parsed NtfsBootSector instance.

        Raises:
            ValueError: When the volume is not a valid NTFS filesystem.
        """
        raw = read_exact(device, 0, 512)

        if read_le16(raw, NTFS_BOOT_SIGNATURE_OFFSET) != NTFS_BOOT_SIGNATURE:
            raise ValueError("Not an NTFS volume (missing 0xAA55 boot signature)")
        if raw[3:11] != NTFS_OEM_ID:
            raise ValueError("Not an NTFS volume (missing 'NTFS    ' OEM id)")

        bytes_per_sector = read_le16(raw, 0x0B)
        sectors_per_cluster = raw[0x0D]
        total_sectors = read_le64(raw, 0x28)
        mft_lcn = read_le64(raw, 0x30)
        mft_mirr_lcn = read_le64(raw, 0x38)

        # MFT record size is stored as a signed byte: a positive value is a
        # cluster count (size = value * cluster_size); a negative value N
        # (two's complement) means size = 2 ** abs(N), independent of cluster
        # size — this is how a 1024-byte MFT record survives on a filesystem
        # with a much larger cluster size.
        mft_record_size_raw = struct.unpack_from("<b", raw, 0x40)[0]
        if mft_record_size_raw < 0:
            mft_record_size = 1 << (-mft_record_size_raw)
        else:
            mft_record_size = mft_record_size_raw * sectors_per_cluster * bytes_per_sector

        if bytes_per_sector == 0 or sectors_per_cluster == 0 or mft_record_size == 0:
            raise ValueError("Invalid NTFS boot sector: zero-sized sector/cluster/record field")

        return cls(
            bytes_per_sector=bytes_per_sector,
            sectors_per_cluster=sectors_per_cluster,
            total_sectors=total_sectors,
            mft_lcn=mft_lcn,
            mft_mirr_lcn=mft_mirr_lcn,
            mft_record_size=mft_record_size,
        )

    @property
    def cluster_size(self) -> int:
        """Cluster size in bytes."""
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def mft_byte_offset(self) -> int:
        """Absolute byte offset of the start of the $MFT (its first record, #0)."""
        return self.mft_lcn * self.cluster_size
