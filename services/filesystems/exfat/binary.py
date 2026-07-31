"""
Binary helpers and constants for exFAT on-disk structures.
"""

import struct
from typing import BinaryIO

from utils.device_io import read_with_timeout

EXFAT_BOOT_SIGNATURE = 0xAA55
EXFAT_BOOT_SIGNATURE_OFFSET = 0x1FE
EXFAT_OEM_ID = b"EXFAT   "

FAT_FREE_CLUSTER = 0x00000000
FAT_BAD_CLUSTER = 0xFFFFFFF7
FAT_MIN_EOC = 0xFFFFFFF8  # values at/above this (0xFFFFFFFF is the canonical marker) end a chain

DIR_ENTRY_SIZE = 32

# EntryType bit layout: bit 7 (0x80) is InUse — cleared on delete, everything
# else in the 32 bytes (name, size, starting cluster) is left untouched. The
# low 7 bits are the type code, checked with ENTRY_TYPE_MASK so deleted and
# in-use entries compare equal.
ENTRY_IN_USE_BIT = 0x80
ENTRY_TYPE_MASK = 0x7F
ENTRY_TYPE_END_OF_DIRECTORY = 0x00
ENTRY_TYPE_FILE = 0x05  # 0x85 with the InUse bit set
ENTRY_TYPE_STREAM_EXTENSION = 0x40  # 0xC0 with the InUse bit set
ENTRY_TYPE_FILE_NAME = 0x41  # 0xC1 with the InUse bit set

FILE_ATTR_DIRECTORY = 0x10

# Stream Extension GeneralSecondaryFlags: bit 1 means the file's clusters are
# contiguous and were never given FAT chain entries at all (an allocation
# optimization), so recovering it must never consult the FAT.
STREAM_FLAG_NO_FAT_CHAIN = 0x02

FILE_NAME_CHARS_PER_ENTRY = 15


def read_le16(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 16-bit integer."""
    return struct.unpack_from("<H", data, offset)[0]


def read_le32(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 32-bit integer."""
    return struct.unpack_from("<I", data, offset)[0]


def read_le64(data: bytes, offset: int) -> int:
    """Read a little-endian unsigned 64-bit integer."""
    return struct.unpack_from("<Q", data, offset)[0]


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
