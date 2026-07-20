"""
Hex dump helper for the file details panel.
"""

from typing import Optional


def format_hex_preview(path: Optional[str], max_bytes: int = 256) -> str:
    """
    Read the first bytes of a file and format them as a hex dump.

    Args:
        path: Absolute path to the file, or None.
        max_bytes: Maximum number of bytes to read.

    Returns:
        Multi-line hex dump string, or a placeholder when unavailable.
    """
    if not path:
        return "—"

    try:
        with open(path, "rb") as handle:
            data = handle.read(max_bytes)
    except OSError:
        return "Could not read file header."

    if not data:
        return "Empty file."

    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_part = " ".join(f"{byte:02x}" for byte in chunk)
        hex_part = hex_part.ljust(16 * 3 - 1)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
        lines.append(f"{offset:08x}  {hex_part}  |{ascii_part}|")

    if len(data) >= max_bytes:
        lines.append(f"... ({max_bytes} bytes shown)")
    return "\n".join(lines)
