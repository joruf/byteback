"""
Binary helpers and constants for FAT32 on-disk structures.
"""

import struct
from typing import BinaryIO

from utils.device_io import read_with_timeout

FAT32_BOOT_SIGNATURE = 0xAA55
FAT32_BOOT_SIGNATURE_OFFSET = 0x1FE
FAT32_FS_TYPE_OFFSET = 0x52
FAT32_FS_TYPE_LABEL = b"FAT32   "

FAT_ENTRY_MASK = 0x0FFFFFFF
FAT_FREE_CLUSTER = 0x00000000
FAT_BAD_CLUSTER = 0x0FFFFFF7
FAT_MIN_EOC = 0x0FFFFFF8  # values at/above this (after masking) mark end-of-chain

DIR_ENTRY_SIZE = 32
DIR_ENTRY_FREE = 0x00  # first byte: no more entries in this directory
DIR_ENTRY_DELETED = 0xE5  # first byte: deleted entry

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME_ID = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LONG_NAME = ATTR_READ_ONLY | ATTR_HIDDEN | ATTR_SYSTEM | ATTR_VOLUME_ID  # 0x0F


def read_le16(data: bytes, offset: int) -> int:
    """Read a little-endian 16-bit integer."""
    return struct.unpack_from("<H", data, offset)[0]


def read_le32(data: bytes, offset: int) -> int:
    """Read a little-endian 32-bit integer."""
    return struct.unpack_from("<I", data, offset)[0]


def read_exact(device: BinaryIO, offset: int, size: int) -> bytes:
    """
    Read exactly ``size`` bytes at an absolute byte offset.

    Args:
        device: Open binary device/image handle.
        offset: Absolute byte offset to seek to first.
        size: Number of bytes to read.

    Returns:
        Raw bytes read.

    Raises:
        OSError: When fewer than ``size`` bytes could be read, including a read
            that hangs past the shared device-read timeout (``TimeoutError`` is
            an ``OSError`` subclass, so existing callers already handle it).
    """
    device.seek(offset)
    data = read_with_timeout(device, size)
    if len(data) != size:
        raise OSError(f"Could not read {size} bytes at offset {offset}")
    return data
