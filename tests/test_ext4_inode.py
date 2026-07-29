"""
Unit tests for ext4 inode block resolution: indirect-block chains and
multi-level extent trees.

These build synthetic in-memory "devices" (io.BytesIO) with hand-crafted
block-pointer structures instead of going through mkfs.ext4/debugfs, so they
run everywhere and exercise tree shapes (extent depth >= 2, triple-indirect)
that would be impractical to coax a real filesystem into producing.
"""

import io
import struct

from services.filesystems.ext4.binary import EXT4_EXT_MAGIC, EXT4_EXTENTS_FL
from services.filesystems.ext4.inode import Ext4Inode
from services.filesystems.ext4.superblock import Ext4Superblock

BLOCK_SIZE = 1024


def make_superblock() -> Ext4Superblock:
    """Build a minimal, self-consistent Ext4Superblock for unit tests."""
    return Ext4Superblock(
        block_size=BLOCK_SIZE,
        inode_size=128,
        inodes_per_group=128,
        blocks_per_group=8192,
        first_inode=11,
        inode_count=128,
        block_count=8192,
        group_count=1,
        feature_incompat=0,
        descriptor_size=32,
    )


def make_device(blocks: dict) -> io.BytesIO:
    """
    Build an in-memory device with the given block contents.

    Args:
        blocks: Mapping of block number -> raw block bytes (padded to BLOCK_SIZE).
    """
    highest_block = max(blocks) if blocks else 0
    buffer = bytearray((highest_block + 1) * BLOCK_SIZE)
    for block_number, data in blocks.items():
        assert len(data) <= BLOCK_SIZE
        padded = data.ljust(BLOCK_SIZE, b"\x00")
        offset = block_number * BLOCK_SIZE
        buffer[offset : offset + BLOCK_SIZE] = padded
    return io.BytesIO(bytes(buffer))


def extent_header(entries: int, depth: int, max_entries: int = 4) -> bytes:
    """Pack a 12-byte ext4 extent-tree header."""
    return struct.pack("<HHHHI", EXT4_EXT_MAGIC, entries, max_entries, depth, 0)


def extent_index_entry(logical_block: int, child_block: int) -> bytes:
    """Pack a 12-byte extent index (interior-node) entry."""
    return struct.pack("<IIHH", logical_block, child_block & 0xFFFFFFFF, child_block >> 32, 0)


def extent_leaf_entry(logical_block: int, length: int, physical_start: int) -> bytes:
    """Pack a 12-byte extent leaf entry."""
    return struct.pack("<IHHI", logical_block, length, physical_start >> 32, physical_start & 0xFFFFFFFF)


def make_raw_inode(size: int, flags: int, i_block: bytes) -> bytes:
    """Build raw inode bytes with size/flags set and i_block starting at 0x28."""
    raw = bytearray(160)
    struct.pack_into("<H", raw, 0x00, 0x8000)  # regular file mode
    struct.pack_into("<I", raw, 0x04, size & 0xFFFFFFFF)
    struct.pack_into("<I", raw, 0x20, flags)
    raw[0x28 : 0x28 + len(i_block)] = i_block
    return bytes(raw)


class TestExtentTreeDepth:
    """A depth-2 extent tree (root -> interior -> leaf) must resolve to real data blocks."""

    def test_resolves_depth_two_extent_tree(self):
        superblock = make_superblock()

        leaf_block_num = 10
        interior_block_num = 5

        leaf_block = extent_header(entries=1, depth=0) + extent_leaf_entry(
            logical_block=0, length=3, physical_start=20
        )
        interior_block = extent_header(entries=1, depth=1) + extent_index_entry(
            logical_block=0, child_block=leaf_block_num
        )

        device = make_device(
            {
                interior_block_num: interior_block,
                leaf_block_num: leaf_block,
                20: b"A" * BLOCK_SIZE,
                21: b"B" * BLOCK_SIZE,
                22: b"C" * BLOCK_SIZE,
            }
        )

        root_header = extent_header(entries=1, depth=2)
        root_entry = extent_index_entry(logical_block=0, child_block=interior_block_num)
        raw_inode = make_raw_inode(
            size=3 * BLOCK_SIZE, flags=EXT4_EXTENTS_FL, i_block=root_header + root_entry
        )

        inode = Ext4Inode.parse(inode_number=12, raw=raw_inode)
        data = inode.read_file_data(device, superblock, raw_inode)

        assert data == b"A" * BLOCK_SIZE + b"B" * BLOCK_SIZE + b"C" * BLOCK_SIZE

    def test_ignores_unknown_child_magic(self):
        """A corrupt child node (bad magic) is skipped instead of crashing the walk."""
        superblock = make_superblock()
        interior_block_num = 5
        device = make_device({interior_block_num: b"\x00" * 12})

        root_header = extent_header(entries=1, depth=1)
        root_entry = extent_index_entry(logical_block=0, child_block=interior_block_num)
        raw_inode = make_raw_inode(
            size=BLOCK_SIZE, flags=EXT4_EXTENTS_FL, i_block=root_header + root_entry
        )

        inode = Ext4Inode.parse(inode_number=12, raw=raw_inode)
        data = inode.read_file_data(device, superblock, raw_inode)

        assert data == b""


class TestIndirectBlocks:
    """Non-extent (block-mapped) inodes must follow single/double/triple indirect chains."""

    def test_resolves_direct_single_double_and_triple_indirect_blocks(self):
        superblock = make_superblock()

        single_indirect_block_num = 50
        double_indirect_block_num = 60
        double_indirect_child_num = 70
        triple_indirect_block_num = 80
        triple_indirect_mid_num = 90
        triple_indirect_leaf_num = 95

        blocks = {
            100: b"D" * BLOCK_SIZE,
            101: b"E" * BLOCK_SIZE,
            102: b"F" * BLOCK_SIZE,
            103: b"G" * BLOCK_SIZE,
            104: b"H" * BLOCK_SIZE,
            single_indirect_block_num: struct.pack("<II", 101, 102),
            double_indirect_child_num: struct.pack("<I", 103),
            double_indirect_block_num: struct.pack("<I", double_indirect_child_num),
            triple_indirect_leaf_num: struct.pack("<I", 104),
            triple_indirect_mid_num: struct.pack("<I", triple_indirect_leaf_num),
            triple_indirect_block_num: struct.pack("<I", triple_indirect_mid_num),
        }
        device = make_device(blocks)

        i_block = bytearray(15 * 4)
        struct.pack_into("<I", i_block, 0 * 4, 100)
        struct.pack_into("<I", i_block, 12 * 4, single_indirect_block_num)
        struct.pack_into("<I", i_block, 13 * 4, double_indirect_block_num)
        struct.pack_into("<I", i_block, 14 * 4, triple_indirect_block_num)

        raw_inode = make_raw_inode(size=5 * BLOCK_SIZE, flags=0, i_block=bytes(i_block))

        inode = Ext4Inode.parse(inode_number=13, raw=raw_inode)
        data = inode.read_file_data(device, superblock, raw_inode)

        assert data == (
            b"D" * BLOCK_SIZE
            + b"E" * BLOCK_SIZE
            + b"F" * BLOCK_SIZE
            + b"G" * BLOCK_SIZE
            + b"H" * BLOCK_SIZE
        )

    def test_direct_blocks_only_when_no_indirect_pointers_set(self):
        superblock = make_superblock()
        device = make_device({100: b"Z" * BLOCK_SIZE})

        i_block = bytearray(15 * 4)
        struct.pack_into("<I", i_block, 0, 100)
        raw_inode = make_raw_inode(size=BLOCK_SIZE, flags=0, i_block=bytes(i_block))

        inode = Ext4Inode.parse(inode_number=14, raw=raw_inode)
        data = inode.read_file_data(device, superblock, raw_inode)

        assert data == b"Z" * BLOCK_SIZE
