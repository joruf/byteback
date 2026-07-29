"""
Crash-safe JSON persistence helpers.
"""

import json
import os
import tempfile
from typing import Any


def write_json_atomic(path: str, payload: Any) -> None:
    """
    Write JSON to ``path`` atomically via a temp file + ``os.replace``.

    A process kill or crash mid-write leaves either the previous complete file
    or the new complete file, never a truncated/corrupt one.

    Args:
        path: Destination file path.
        payload: JSON-serializable data.
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
