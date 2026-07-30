"""
Binary helpers for ext4 on-disk structures.
"""

import struct
from typing import BinaryIO, Tuple

from utils.device_io import read_with_timeout


EXT4_SUPERBLOCK_OFFSET = 1024
EXT4_MAGIC = 0xEF53
EXT4_EXTENTS_FL = 0x00080000
EXT4_EXT_MAGIC = 0xF30A
EXT4_S_IFREG = 0x8000
EXT4_S_IFDIR = 0x4000
EXT4_S_IFLNK = 0xA000
EXT4_FEATURE_INCOMPAT_64BIT = 0x0080
EXT4_MIN_GROUP_DESC_SIZE = 32
EXT4_MIN_GROUP_DESC_SIZE_64BIT = 64


def read_le16(data: bytes, offset: int) -> int:
    """Read a little-endian 16-bit integer."""
    return struct.unpack_from("<H", data, offset)[0]


def read_le32(data: bytes, offset: int) -> int:
    """Read a little-endian 32-bit integer."""
    return struct.unpack_from("<I", data, offset)[0]


def read_block(device: BinaryIO, block_size: int, block_number: int) -> bytes:
    """
    Read one filesystem block from a device or image file.

    Args:
        device: Open binary device/image handle.
        block_size: Filesystem block size in bytes.
        block_number: Logical block number.

    Returns:
        Raw block bytes.

    Raises:
        OSError: When the block cannot be read, including a read that hangs
            past ``DEVICE_READ_TIMEOUT_SECONDS`` (``TimeoutError`` is an
            ``OSError`` subclass, so existing callers already handle it).
    """
    device.seek(block_number * block_size)
    data = read_with_timeout(device, block_size)
    if len(data) != block_size:
        raise OSError(f"Could not read block {block_number}")
    return data


def block_ranges_to_byte_ranges(
    ranges: list,
    block_size: int,
) -> list:
    """
    Convert block ranges to byte offsets and lengths.

    Args:
        ranges: List of (start_block, block_count) tuples.
        block_size: Filesystem block size.

    Returns:
        List of (byte_offset, byte_length) tuples.
    """
    return [(start * block_size, count * block_size) for start, count in ranges]
