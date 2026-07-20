"""
File metadata helpers used by scanners and the details panel.
"""

import mimetypes
import os
from datetime import datetime
from typing import Optional


def detect_mime_type(path: str) -> Optional[str]:
    """
    Guess the MIME type of a file from its name and optional magic detection.

    Args:
        path: Absolute filesystem path.

    Returns:
        MIME type string or None when unknown.
    """
    mime, _ = mimetypes.guess_type(path)
    if mime:
        return mime

    try:
        import magic

        return magic.from_file(path, mime=True)
    except (ImportError, OSError, AttributeError):
        return None


def format_timestamp(epoch: float) -> str:
    """
    Format a UNIX timestamp for display.

    Args:
        epoch: Seconds since 1970-01-01.

    Returns:
        ISO-like local datetime string.
    """
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def human_size(size_bytes: int) -> str:
    """
    Convert bytes to a compact human-readable size.

    Args:
        size_bytes: Size in bytes.

    Returns:
        Formatted string such as ``12.4 MiB``.
    """
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{size_bytes} B"
