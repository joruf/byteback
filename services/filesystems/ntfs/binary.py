"""
Binary helpers and constants for NTFS on-disk structures.
"""

import struct
from typing import BinaryIO

from utils.device_io import read_with_timeout

NTFS_OEM_ID = b"NTFS    "
NTFS_BOOT_SIGNATURE = 0xAA55
NTFS_BOOT_SIGNATURE_OFFSET = 0x1FE

MFT_RECORD_MAGIC = b"FILE"
MFT_RECORD_MAGIC_BAD = b"BAAD"

MFT_RECORD_IN_USE = 0x0001
MFT_RECORD_IS_DIRECTORY = 0x0002

ATTR_STANDARD_INFORMATION = 0x10
ATTR_FILE_NAME = 0x30
ATTR_DATA = 0x80
ATTR_END_MARKER = 0xFFFFFFFF

# $FILE_NAME namespace: 0=POSIX, 1=Win32, 2=DOS (8.3 short name), 3=Win32 & DOS.
FILE_NAME_TYPE_DOS_ONLY = 2


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
