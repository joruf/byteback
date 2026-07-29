"""
ext4 superblock and block-group descriptor parsing.
"""

import math
from dataclasses import dataclass
from typing import BinaryIO, List

from services.filesystems.ext4.binary import (
    EXT4_FEATURE_INCOMPAT_64BIT,
    EXT4_MAGIC,
    EXT4_MIN_GROUP_DESC_SIZE,
    EXT4_MIN_GROUP_DESC_SIZE_64BIT,
    EXT4_SUPERBLOCK_OFFSET,
    read_block,
    read_le16,
    read_le32,
)


@dataclass
class Ext4GroupDescriptor:
    """
    Parsed ext4 block-group descriptor.

    Attributes:
        index: Block group number.
        block_bitmap_block: Block number of the free-block bitmap.
        inode_bitmap_block: Block number of the free-inode bitmap.
        inode_table_block: Starting block of the inode table.
        free_blocks: Number of free blocks in this group.
        free_inodes: Number of free inodes in this group.
    """

    index: int
    block_bitmap_block: int
    inode_bitmap_block: int
    inode_table_block: int
    free_blocks: int
    free_inodes: int


@dataclass
class Ext4Superblock:
    """
    Parsed ext4 superblock fields required for recovery scanning.

    Attributes:
        block_size: Filesystem block size in bytes.
        inode_size: Size of one inode structure in bytes.
        inodes_per_group: Number of inodes per block group.
        blocks_per_group: Number of blocks per block group.
        first_inode: First usable inode number (usually 11).
        inode_count: Total inode count.
        block_count: Total block count.
        group_count: Number of block groups.
        feature_incompat: Incompatible feature flags.
        descriptor_size: Size in bytes of one block-group descriptor.
    """

    block_size: int
    inode_size: int
    inodes_per_group: int
    blocks_per_group: int
    first_inode: int
    inode_count: int
    block_count: int
    group_count: int
    feature_incompat: int
    descriptor_size: int

    @classmethod
    def read_from_device(cls, device: BinaryIO) -> "Ext4Superblock":
        """
        Parse the superblock from a block device or image file.

        Args:
            device: Open binary handle positioned at the start of the volume.

        Returns:
            Parsed Ext4Superblock instance.

        Raises:
            ValueError: When the volume is not a valid ext4 filesystem.
        """
        device.seek(EXT4_SUPERBLOCK_OFFSET)
        raw = device.read(1024)
        if len(raw) < 1024:
            raise ValueError("Volume too small for an ext4 superblock")

        magic = read_le16(raw, 0x038)
        if magic != EXT4_MAGIC:
            raise ValueError(f"Not an ext4 filesystem (magic 0x{magic:04x})")

        inode_count = read_le32(raw, 0x000)
        block_count = read_le32(raw, 0x004)
        log_block_size = read_le32(raw, 0x018)
        block_size = 1024 << log_block_size
        blocks_per_group = read_le32(raw, 0x020)
        inodes_per_group = read_le32(raw, 0x028)
        first_inode = read_le32(raw, 0x054)
        inode_size = read_le16(raw, 0x058) or 128
        feature_incompat = read_le32(raw, 0x060)

        if blocks_per_group == 0:
            blocks_per_group = block_size * 8
        if inodes_per_group == 0:
            inodes_per_group = block_size // inode_size

        group_count = math.ceil(block_count / blocks_per_group)

        if feature_incompat & EXT4_FEATURE_INCOMPAT_64BIT:
            descriptor_size = read_le16(raw, 0x0FE) or EXT4_MIN_GROUP_DESC_SIZE_64BIT
        else:
            descriptor_size = EXT4_MIN_GROUP_DESC_SIZE

        return cls(
            block_size=block_size,
            inode_size=inode_size,
            inodes_per_group=inodes_per_group,
            blocks_per_group=blocks_per_group,
            first_inode=first_inode,
            inode_count=inode_count,
            block_count=block_count,
            group_count=group_count,
            feature_incompat=feature_incompat,
            descriptor_size=descriptor_size,
        )

    def load_group_descriptors(self, device: BinaryIO) -> List[Ext4GroupDescriptor]:
        """
        Read all block-group descriptors.

        Uses ``self.descriptor_size`` (32 bytes on classic ext4, 64+ bytes when the
        ``64BIT`` incompat feature is enabled) instead of assuming a fixed layout, and
        combines the high/low 32-bit halves of each block pointer when the descriptor
        is large enough to carry them.

        Args:
            device: Open binary device/image handle.

        Returns:
            List of group descriptors.
        """
        descriptor_size = self.descriptor_size
        count = self.group_count
        table_bytes = count * descriptor_size
        table_block = 2 if self.block_size == 1024 else 1

        raw = b""
        blocks_needed = math.ceil(table_bytes / self.block_size)
        for offset in range(blocks_needed):
            raw += read_block(device, self.block_size, table_block + offset)

        has_hi_fields = descriptor_size >= EXT4_MIN_GROUP_DESC_SIZE_64BIT

        groups: List[Ext4GroupDescriptor] = []
        for index in range(count):
            offset = index * descriptor_size
            chunk = raw[offset : offset + descriptor_size]

            block_bitmap = read_le32(chunk, 0x00)
            inode_bitmap = read_le32(chunk, 0x04)
            inode_table = read_le32(chunk, 0x08)
            free_blocks = read_le16(chunk, 0x0C)
            free_inodes = read_le16(chunk, 0x0E)

            if has_hi_fields:
                block_bitmap |= read_le32(chunk, 0x20) << 32
                inode_bitmap |= read_le32(chunk, 0x24) << 32
                inode_table |= read_le32(chunk, 0x28) << 32
                free_blocks |= read_le16(chunk, 0x2C) << 16
                free_inodes |= read_le16(chunk, 0x2E) << 16

            groups.append(
                Ext4GroupDescriptor(
                    index=index,
                    block_bitmap_block=block_bitmap,
                    inode_bitmap_block=inode_bitmap,
                    inode_table_block=inode_table,
                    free_blocks=free_blocks,
                    free_inodes=free_inodes,
                )
            )
        return groups
