"""
Open block devices and image files with optional root-helper fallback.
"""

import concurrent.futures
import os
from typing import Any, BinaryIO, Optional

from config.scan_settings import DEVICE_READ_TIMEOUT_SECONDS
from services.root_helper import ROOT_HELPER

# Shared pool for read_with_timeout(): reused across calls so the common (fast,
# healthy-device) case only pays a queue-dispatch cost, not a thread-creation
# cost per read — scans can issue many thousands of small reads. Sized well
# above the ~1 concurrent read a single scan/imaging operation issues at a
# time, so a handful of stuck reads don't starve the next call.
_READ_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8, thread_name_prefix="byteback-read-timeout"
)


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


def read_with_timeout(handle: Any, size: int, timeout: float = DEVICE_READ_TIMEOUT_SECONDS) -> bytes:
    """
    Read from a device/file handle with a wall-clock timeout.

    A failing or hanging drive can cause a plain ``handle.read()`` to block
    forever inside the kernel, with no way for the caller to interrupt it —
    that leaves scans/imaging stuck and unresponsive to Cancel. This runs the
    read on a background daemon thread and gives up waiting after ``timeout``
    seconds, letting the caller regain control (and honor cancellation)
    instead of hanging indefinitely.

    Note: Python cannot forcibly abort a blocked syscall, so a genuinely stuck
    read keeps running in its background thread until the OS unblocks it (or
    the process exits) — this bounds how long the *caller* waits, it does not
    kill the underlying I/O.

    Args:
        handle: Open file-like object with a ``read(size)`` method.
        size: Number of bytes to request.
        timeout: Seconds to wait before giving up.

    Returns:
        Bytes read.

    Raises:
        TimeoutError: When the read does not complete within ``timeout`` seconds.
        OSError: Any error the underlying read itself raises.
    """
    future = _READ_EXECUTOR.submit(handle.read, size)
    try:
        return future.result(timeout=timeout)
    except concurrent.futures.TimeoutError as exc:
        raise TimeoutError(f"Read of {size} bytes timed out after {timeout}s") from exc
