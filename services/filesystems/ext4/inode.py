"""
ext4 inode parsing and file data extraction.
"""

from dataclasses import dataclass
from typing import BinaryIO, List, Tuple

from services.filesystems.ext4.binary import (
    EXT4_EXT_MAGIC,
    EXT4_EXTENTS_FL,
    EXT4_S_IFDIR,
    EXT4_S_IFLNK,
    EXT4_S_IFREG,
    read_block,
    read_le16,
    read_le32,
)
from services.filesystems.ext4.superblock import Ext4Superblock


@dataclass
class Ext4Inode:
    """
    Parsed ext4 inode with enough metadata for deleted-file recovery.

    Attributes:
        inode_number: Absolute inode index on the filesystem.
        mode: File type and permission bits.
        size: File size in bytes.
        links_count: Hard link count (0 for deleted inodes).
        deletion_time: Deletion timestamp (0 when not deleted).
        flags: Inode flags (extent tree, etc.).
        is_deleted: True when the inode appears deleted.
        is_regular_file: True for regular files.
    """

    inode_number: int
    mode: int
    size: int
    links_count: int
    deletion_time: int
    flags: int

    @property
    def is_deleted(self) -> bool:
        """True when link count is zero and deletion time is set."""
        return self.links_count == 0 and self.deletion_time != 0 and self.mode != 0

    @property
    def is_regular_file(self) -> bool:
        """True for regular file inodes."""
        return (self.mode & 0xF000) == EXT4_S_IFREG

    @property
    def is_directory(self) -> bool:
        """True for directory inodes."""
        return (self.mode & 0xF000) == EXT4_S_IFDIR

    @property
    def is_symlink(self) -> bool:
        """True for symbolic link inodes."""
        return (self.mode & 0xF000) == EXT4_S_IFLNK

    @classmethod
    def parse(cls, inode_number: int, raw: bytes) -> "Ext4Inode":
        """
        Parse raw inode bytes into an Ext4Inode instance.

        Args:
            inode_number: Absolute inode number.
            raw: Raw inode structure bytes.

        Returns:
            Parsed Ext4Inode.
        """
        mode = read_le16(raw, 0x00)
        size_lo = read_le32(raw, 0x04)
        size_hi = read_le32(raw, 0x6C) if len(raw) >= 0x70 else 0
        size = size_lo | (size_hi << 32)
        deletion_time = read_le32(raw, 0x14)
        links_count = read_le16(raw, 0x1A)
        flags = read_le32(raw, 0x20)

        return cls(
            inode_number=inode_number,
            mode=mode,
            size=size,
            links_count=links_count,
            deletion_time=deletion_time,
            flags=flags,
        )

    def read_file_data(
        self,
        device: BinaryIO,
        superblock: Ext4Superblock,
        raw_inode: bytes,
    ) -> bytes:
        """
        Read file content from direct blocks or an extent tree.

        Args:
            device: Open device/image handle.
            superblock: Parsed superblock for block size.
            raw_inode: Raw inode bytes containing block pointers.

        Returns:
            File content bytes (may be shorter than inode size on error).
        """
        if self.size <= 0:
            return b""

        max_read = min(self.size, 100 * 1024 * 1024)
        if self.flags & EXT4_EXTENTS_FL:
            blocks = self._collect_extent_blocks(raw_inode, superblock, device)
        else:
            blocks = self._collect_direct_blocks(raw_inode)

        chunks: List[bytes] = []
        remaining = max_read
        for block_number in blocks:
            if remaining <= 0:
                break
            try:
                data = read_block(device, superblock.block_size, block_number)
            except OSError:
                break
            take = min(len(data), remaining)
            chunks.append(data[:take])
            remaining -= take

        return b"".join(chunks)[:max_read]

    def _collect_direct_blocks(self, raw_inode: bytes) -> List[int]:
        """Collect block numbers from the traditional i_block array."""
        blocks: List[int] = []
        for index in range(15):
            offset = 0x28 + (index * 4)
            if offset + 4 > len(raw_inode):
                break
            block_number = read_le32(raw_inode, offset)
            if block_number:
                blocks.append(block_number)
        return blocks

    def _collect_extent_blocks(
        self,
        raw_inode: bytes,
        superblock: Ext4Superblock,
        device: BinaryIO,
    ) -> List[int]:
        """Walk an extent tree and return physical block numbers."""
        header = raw_inode[0x28 : 0x28 + 12]
        magic = read_le16(header, 0x00)
        if magic != EXT4_EXT_MAGIC:
            return []

        depth = read_le16(header, 0x06)
        entries = read_le16(header, 0x02)
        if depth == 0:
            return self._expand_extent_entries(raw_inode[0x28 + 12 : 0x28 + 12 + entries * 12])

        blocks: List[int] = []
        entry_offset = 0x28 + 12
        for _index in range(entries):
            entry = raw_inode[entry_offset : entry_offset + 12]
            entry_offset += 12
            leaf_block = read_le32(entry, 0x04)
            leaf_raw = read_block(device, superblock.block_size, leaf_block)
            leaf_magic = read_le16(leaf_raw, 0x00)
            if leaf_magic != EXT4_EXT_MAGIC:
                continue
            leaf_entries = read_le16(leaf_raw, 0x02)
            blocks.extend(self._expand_extent_entries(leaf_raw[12 : 12 + leaf_entries * 12]))
        return blocks

    @staticmethod
    def _expand_extent_entries(raw_entries: bytes) -> List[int]:
        """Expand extent entries into a flat physical block list."""
        blocks: List[int] = []
        for offset in range(0, len(raw_entries), 12):
            if offset + 12 > len(raw_entries):
                break
            entry = raw_entries[offset : offset + 12]
            length = read_le16(entry, 0x04)
            start_hi = read_le16(entry, 0x06)
            start_lo = read_le32(entry, 0x08)
            physical_start = start_lo | (start_hi << 32)
            length_blocks = length & 0x7FFF
            for block_index in range(length_blocks):
                blocks.append(physical_start + block_index)
        return blocks


def read_inode_raw(
    device: BinaryIO,
    superblock: Ext4Superblock,
    inode_number: int,
) -> bytes:
    """
    Read raw inode bytes for the given inode number.

    Args:
        device: Open device/image handle.
        superblock: Parsed superblock.
        inode_number: Absolute inode number (1-based).

    Returns:
        Raw inode structure bytes.
    """
    if inode_number < 1:
        raise ValueError("Invalid inode number")

    group_index = (inode_number - 1) // superblock.inodes_per_group
    index_in_group = (inode_number - 1) % superblock.inodes_per_group
    groups = superblock.load_group_descriptors(device)
    if group_index >= len(groups):
        raise ValueError("Inode group out of range")

    table_block = groups[group_index].inode_table_block
    byte_offset = (
        table_block * superblock.block_size + index_in_group * superblock.inode_size
    )
    device.seek(byte_offset)
    raw = device.read(superblock.inode_size)
    if len(raw) < superblock.inode_size:
        raise OSError(f"Could not read inode {inode_number}")
    return raw
