"""
FAT32 directory entry parsing.
"""

from dataclasses import dataclass
from typing import BinaryIO, List

from services.filesystems.fat32.binary import (
    ATTR_DIRECTORY,
    ATTR_LONG_NAME,
    ATTR_VOLUME_ID,
    DIR_ENTRY_DELETED,
    DIR_ENTRY_FREE,
    DIR_ENTRY_SIZE,
    read_le16,
    read_le32,
)
from services.filesystems.fat32.boot_sector import Fat32BootSector


@dataclass
class Fat32DirEntry:
    """
    One parsed 32-byte FAT32 short (8.3) directory entry.

    Attributes:
        name: Dot-joined 8.3 name. For a deleted entry the unrecoverable first
            character is replaced with ``_`` (the on-disk byte itself, 0xE5,
            carries no information about what it originally was).
        is_directory: True for a subdirectory entry.
        is_deleted: True when the entry's first byte marks it as deleted.
        is_volume_label: True for the volume-label entry (never a real file).
        is_long_name_part: True for a long-filename fragment, not a real entry.
        first_cluster: Starting cluster of the entry's data (0 for empty files).
        file_size: File size in bytes (0 for directories).
    """

    name: str
    is_directory: bool
    is_deleted: bool
    is_volume_label: bool
    is_long_name_part: bool
    first_cluster: int
    file_size: int

    @classmethod
    def parse(cls, raw: bytes) -> "Fat32DirEntry":
        """
        Parse one 32-byte directory entry.

        Args:
            raw: Exactly 32 bytes starting at the entry.

        Returns:
            Parsed Fat32DirEntry.
        """
        if len(raw) < DIR_ENTRY_SIZE:
            raise ValueError("Directory entry too short")

        attr = raw[11]
        is_deleted = raw[0] == DIR_ENTRY_DELETED
        is_long_name_part = attr == ATTR_LONG_NAME
        is_volume_label = bool(attr & ATTR_VOLUME_ID) and not is_long_name_part
        is_directory = bool(attr & ATTR_DIRECTORY) and not is_long_name_part

        raw_name = bytearray(raw[0:8])
        if is_deleted:
            raw_name[0] = ord("_")
        name_part = bytes(raw_name).decode("ascii", errors="replace").rstrip()
        ext_part = raw[8:11].decode("ascii", errors="replace").rstrip()
        name = f"{name_part}.{ext_part}" if ext_part else name_part

        first_cluster = (read_le16(raw, 20) << 16) | read_le16(raw, 26)
        file_size = read_le32(raw, 28)

        return cls(
            name=name,
            is_directory=is_directory,
            is_deleted=is_deleted,
            is_volume_label=is_volume_label,
            is_long_name_part=is_long_name_part,
            first_cluster=first_cluster,
            file_size=file_size,
        )

    @property
    def is_dot_entry(self) -> bool:
        """True for the "." and ".." self/parent-directory entries."""
        return self.name in (".", "..")


def list_directory_entries(
    boot_sector: Fat32BootSector,
    device: BinaryIO,
    cluster: int,
) -> List[Fat32DirEntry]:
    """
    Parse every directory entry in the cluster chain starting at ``cluster``.

    Args:
        boot_sector: Parsed FAT32 boot sector.
        device: Open device/image handle.
        cluster: Starting cluster of the directory (root or subdirectory).

    Returns:
        Parsed entries in on-disk order, stopping at the first "no more
        entries" marker (or at the end of the chain).
    """
    data = boot_sector.read_cluster_chain_data(device, cluster)
    entries: List[Fat32DirEntry] = []

    for offset in range(0, len(data) - DIR_ENTRY_SIZE + 1, DIR_ENTRY_SIZE):
        raw = data[offset : offset + DIR_ENTRY_SIZE]
        if raw[0] == DIR_ENTRY_FREE:
            break
        entries.append(Fat32DirEntry.parse(raw))

    return entries
