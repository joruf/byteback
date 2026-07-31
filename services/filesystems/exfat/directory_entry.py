"""
exFAT directory entry parsing.

Unlike FAT32's flat 32-byte entries, one exFAT file is a *set* of consecutive
32-byte slots: a File Directory Entry (primary), followed by a Stream
Extension entry (name length/hash, starting cluster, data length, allocation
flags), followed by one or more File Name entries (15 UTF-16 code units
each). Deleting a file clears bit 0x80 (InUse) on every slot in the set but
leaves everything else — including ``SecondaryCount``, so the set's size on
disk is still known — untouched until the slots are reused.
"""

from dataclasses import dataclass
from typing import BinaryIO, List, Optional

from services.filesystems.exfat.binary import (
    DIR_ENTRY_SIZE,
    ENTRY_IN_USE_BIT,
    ENTRY_TYPE_END_OF_DIRECTORY,
    ENTRY_TYPE_FILE,
    ENTRY_TYPE_FILE_NAME,
    ENTRY_TYPE_MASK,
    ENTRY_TYPE_STREAM_EXTENSION,
    FILE_ATTR_DIRECTORY,
    FILE_NAME_CHARS_PER_ENTRY,
    STREAM_FLAG_NO_FAT_CHAIN,
    read_exact,
    read_le16,
    read_le32,
    read_le64,
)
from services.filesystems.exfat.boot_sector import ExfatBootSector


@dataclass
class ExfatDirEntry:
    """
    One parsed exFAT file (aggregated from its File + Stream Extension +
    File Name directory-entry set).

    Attributes:
        name: Full filename, decoded from the File Name secondary entries.
        is_directory: True for a subdirectory entry.
        is_deleted: True when the set's InUse bit is cleared.
        first_cluster: Starting cluster of the entry's data (0 if empty).
        data_length: Content size in bytes.
        no_fat_chain: True when clusters are contiguous and were never given
            FAT chain entries (must be read without consulting the FAT).
    """

    name: str
    is_directory: bool
    is_deleted: bool
    first_cluster: int
    data_length: int
    no_fat_chain: bool


def _read_directory_data(
    boot_sector: ExfatBootSector,
    device: BinaryIO,
    cluster: int,
    data_length: Optional[int],
    no_fat_chain: bool,
) -> bytes:
    """
    Read a directory's raw entry bytes.

    Args:
        boot_sector: Parsed exFAT boot sector.
        device: Open device/image handle.
        cluster: Starting cluster of the directory.
        data_length: Stored size in bytes, or ``None`` for the root directory
            (which has no size of its own — its end is the natural end of its
            FAT chain, same as the "no more entries" marker inside it).
        no_fat_chain: True when the directory's clusters are contiguous and
            must be read without consulting the FAT (irrelevant for root,
            which is always FAT-chain-walked).
    """
    cluster_size = boot_sector.cluster_size

    if data_length is None:
        return boot_sector.read_cluster_chain_data(device, cluster)

    if no_fat_chain:
        needed = -(-data_length // cluster_size)  # ceil division
        start = cluster if cluster >= 2 else 2
        clusters = list(range(start, start + needed))
    else:
        clusters = boot_sector.walk_cluster_chain(device, cluster)

    chunks = [read_exact(device, boot_sector.cluster_to_byte_offset(c), cluster_size) for c in clusters]
    return b"".join(chunks)[:data_length]


def list_directory_entries(
    boot_sector: ExfatBootSector,
    device: BinaryIO,
    cluster: int,
    data_length: Optional[int] = None,
    no_fat_chain: bool = False,
) -> List[ExfatDirEntry]:
    """
    Parse every file entry in a directory (root or subdirectory).

    Args:
        boot_sector: Parsed exFAT boot sector.
        device: Open device/image handle.
        cluster: Starting cluster of the directory.
        data_length: Stored directory size, or ``None`` for the root
            directory (see ``_read_directory_data``).
        no_fat_chain: True when the directory's clusters are contiguous.

    Returns:
        Parsed file/subdirectory entries in on-disk order (both deleted and
        in-use), stopping at the first "end of directory" marker. Allocation
        Bitmap, Upcase Table, and Volume Label entries are not files and are
        silently skipped.
    """
    data = _read_directory_data(boot_sector, device, cluster, data_length, no_fat_chain)
    entries: List[ExfatDirEntry] = []
    offset = 0

    while offset + DIR_ENTRY_SIZE <= len(data):
        entry_type = data[offset]
        if entry_type == ENTRY_TYPE_END_OF_DIRECTORY:
            break

        if (entry_type & ENTRY_TYPE_MASK) != ENTRY_TYPE_FILE:
            offset += DIR_ENTRY_SIZE
            continue

        secondary_count = data[offset + 1]
        set_size = (1 + secondary_count) * DIR_ENTRY_SIZE
        if secondary_count < 1 or offset + set_size > len(data):
            offset += DIR_ENTRY_SIZE
            continue

        stream_offset = offset + DIR_ENTRY_SIZE
        if (data[stream_offset] & ENTRY_TYPE_MASK) != ENTRY_TYPE_STREAM_EXTENSION:
            offset += set_size
            continue

        is_deleted = not bool(entry_type & ENTRY_IN_USE_BIT)
        file_attributes = read_le16(data, offset + 4)
        is_directory = bool(file_attributes & FILE_ATTR_DIRECTORY)

        general_flags = data[stream_offset + 1]
        name_length = data[stream_offset + 3]
        first_cluster = read_le32(data, stream_offset + 20)
        file_data_length = read_le64(data, stream_offset + 24)
        no_fat_chain_flag = bool(general_flags & STREAM_FLAG_NO_FAT_CHAIN)

        name_units = bytearray()
        remaining_chars = name_length
        name_entry_count = -(-name_length // FILE_NAME_CHARS_PER_ENTRY)  # ceil division
        for i in range(min(name_entry_count, secondary_count - 1)):
            name_offset = stream_offset + (1 + i) * DIR_ENTRY_SIZE
            if (data[name_offset] & ENTRY_TYPE_MASK) != ENTRY_TYPE_FILE_NAME:
                break
            take = min(FILE_NAME_CHARS_PER_ENTRY, remaining_chars)
            name_units += data[name_offset + 2 : name_offset + 2 + take * 2]
            remaining_chars -= take

        try:
            name = bytes(name_units).decode("utf-16-le")
        except UnicodeDecodeError:
            name = ""

        if name:
            entries.append(
                ExfatDirEntry(
                    name=name,
                    is_directory=is_directory,
                    is_deleted=is_deleted,
                    first_cluster=first_cluster,
                    data_length=file_data_length,
                    no_fat_chain=no_fat_chain_flag,
                )
            )

        offset += set_size

    return entries
