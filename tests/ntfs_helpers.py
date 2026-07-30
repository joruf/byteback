"""
Helpers for creating NTFS test images in unit tests.

There is no unprivileged way to mount+delete a file on this platform (ntfs-3g
requires elevated privileges here, unlike mtools for FAT), so deletion is
simulated the same way a real delete does at the metadata level: clearing the
MFT record's InUse flag bit, leaving every attribute (name, content) intact.
The boot-sector/MFT-record arithmetic below is computed independently of
byteback's own services.filesystems.ntfs module, so these tests stay an
honest external check rather than a tautology.
"""

import re
import shutil
import struct
import subprocess
from pathlib import Path


def create_ntfs_image(path: Path, size_mb: int = 64) -> None:
    """
    Create an empty NTFS image file.

    Args:
        path: Output image path.
        size_mb: Image size in megabytes.
    """
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={path}", "bs=1M", f"count={size_mb}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["mkntfs", "-F", "-Q", "-L", "testvol", str(path)],
        check=True,
        capture_output=True,
    )


def write_file_to_image(image_path: Path, image_filename: str, host_file: Path) -> None:
    """
    Copy a host file into an unmounted NTFS image via ntfscp.

    Args:
        image_path: NTFS image path.
        image_filename: Destination path inside the image (e.g. "secret.txt").
        host_file: Local file to write.
    """
    subprocess.run(
        ["ntfscp", "-f", str(image_path), str(host_file), f"/{image_filename}"],
        check=True,
        capture_output=True,
    )


def find_mft_record_number(image_path: Path, image_filename: str) -> int:
    """
    Look up the MFT record number for a file via ntfsinfo.

    Args:
        image_path: NTFS image path.
        image_filename: Path inside the image (e.g. "secret.txt").

    Returns:
        Absolute MFT record number.
    """
    result = subprocess.run(
        ["ntfsinfo", "-m", str(image_path), "-F", f"/{image_filename}"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"Dumping Inode (\d+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not find MFT record number for {image_filename}")
    return int(match.group(1))


def mark_mft_record_deleted(image_path: Path, record_number: int) -> None:
    """
    Clear the InUse flag in one MFT record, simulating a real NTFS delete.

    A real delete only clears this bit (plus updating the parent directory
    index and $Bitmap, neither of which byteback's scanner reads) — every
    attribute in the record, including $FILE_NAME and $DATA, is left as-is
    until the record slot is reused, which is exactly what makes recovery
    possible.

    Args:
        image_path: NTFS image path.
        record_number: Absolute MFT record number to mark deleted.
    """
    with open(image_path, "rb") as handle:
        boot = handle.read(512)

    bytes_per_sector = struct.unpack_from("<H", boot, 0x0B)[0]
    sectors_per_cluster = boot[0x0D]
    mft_lcn = struct.unpack_from("<Q", boot, 0x30)[0]
    mft_record_size_raw = struct.unpack_from("<b", boot, 0x40)[0]
    record_size = (
        1 << (-mft_record_size_raw)
        if mft_record_size_raw < 0
        else mft_record_size_raw * sectors_per_cluster * bytes_per_sector
    )
    cluster_size = sectors_per_cluster * bytes_per_sector
    record_offset = mft_lcn * cluster_size + record_number * record_size

    with open(image_path, "r+b") as handle:
        handle.seek(record_offset + 0x16)
        flags = struct.unpack("<H", handle.read(2))[0]
        handle.seek(record_offset + 0x16)
        handle.write(struct.pack("<H", flags & ~0x0001))


def tools_available() -> bool:
    """Return True when mkntfs, ntfscp, and ntfsinfo are available."""
    return all(shutil.which(tool) is not None for tool in ("mkntfs", "ntfscp", "ntfsinfo"))
