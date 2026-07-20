"""
Open block devices and image files with optional root-helper fallback.
"""

import os
from typing import BinaryIO, Optional

from services.root_helper import ROOT_HELPER


class RootHelperDevice:
    """
    Read-only file-like wrapper that reads through the root helper RPC channel.
    """

    def __init__(self, path: str) -> None:
        """
        Args:
            path: Device or image path to read remotely.
        """
        self._path = path
        self._position = 0
        self._size: Optional[int] = None

    def read(self, size: int = -1) -> bytes:
        """
        Read bytes from the remote device.

        Args:
            size: Number of bytes to read, or -1 for the remainder.

        Returns:
            Bytes read from the device.
        """
        total_size = self._get_size()
        if size < 0:
            size = max(0, total_size - self._position)
        if size == 0:
            return b""

        data = ROOT_HELPER.read(self._path, self._position, size)
        self._position += len(data)
        return data

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        """
        Reposition the read cursor.

        Args:
            offset: Offset value.
            whence: ``os.SEEK_SET``, ``os.SEEK_CUR``, or ``os.SEEK_END``.

        Returns:
            New absolute position.
        """
        total_size = self._get_size()
        if whence == os.SEEK_SET:
            self._position = offset
        elif whence == os.SEEK_CUR:
            self._position += offset
        elif whence == os.SEEK_END:
            self._position = total_size + offset
        else:
            raise ValueError(f"invalid whence: {whence}")

        self._position = max(0, min(self._position, total_size))
        return self._position

    def tell(self) -> int:
        """
        Return the current read position.

        Returns:
            Current offset in bytes.
        """
        return self._position

    def close(self) -> None:
        """No persistent handle is kept on the helper side."""

    def __enter__(self) -> "RootHelperDevice":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _get_size(self) -> int:
        """
        Return and cache the device size.

        Returns:
            Total readable size in bytes.
        """
        if self._size is None:
            self._size = ROOT_HELPER.size(self._path)
        return self._size


def open_device(path: str) -> BinaryIO:
    """
    Open a block device or image file for reading.

    Uses a direct open when permitted, otherwise falls back to the root helper.

    Args:
        path: Device or image path.

    Returns:
        Binary read handle.

    Raises:
        OSError: When the path cannot be opened for reading.
    """
    if os.path.exists(path):
        try:
            handle = open(path, "rb")
            handle.read(512)
            handle.seek(0)
            return handle
        except OSError:
            pass

    if ROOT_HELPER.is_running() and ROOT_HELPER.probe(path):
        return RootHelperDevice(path)

    raise OSError(f"Cannot read device: {path}")
