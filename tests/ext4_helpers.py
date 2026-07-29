"""
Helpers for creating ext4 test images in unit tests.
"""

import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional


def create_ext4_image(path: Path, size_mb: int = 32, extra_mkfs_args: Optional[List[str]] = None) -> None:
    """
    Create an empty ext4 image file.

    Args:
        path: Output image path.
        size_mb: Image size in megabytes.
        extra_mkfs_args: Extra flags passed to ``mkfs.ext4`` (e.g. ``["-O", "^64bit"]``
            to force classic 32-byte group descriptors, or ``["-O", "^extent"]`` to
            force block-mapped, non-extent inodes).
    """
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={path}", "bs=1M", f"count={size_mb}"],
        check=True,
        capture_output=True,
    )
    command = ["mkfs.ext4", "-F", *(extra_mkfs_args or []), str(path)]
    subprocess.run(command, check=True, capture_output=True)


def dumpe2fs_group_descriptors(path: Path) -> List[dict]:
    """
    Parse ground-truth block-group descriptor values straight from ``dumpe2fs``.

    Used to cross-validate ByteBack's own group-descriptor parsing (block bitmap,
    inode bitmap, inode table, free counts) against a trusted independent source,
    forcing English output so parsing does not depend on the host locale.

    Args:
        path: ext4 image path.

    Returns:
        One dict per block group, in group order, with keys ``index``,
        ``block_bitmap_block``, ``inode_bitmap_block``, ``inode_table_block``,
        ``free_blocks``, ``free_inodes``.
    """
    env = {**os.environ, "LC_ALL": "C"}
    result = subprocess.run(
        ["dumpe2fs", str(path)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    groups: List[dict] = []
    current: Optional[dict] = None
    for line in result.stdout.splitlines():
        header = re.match(r"Group (\d+):", line)
        if header:
            if current is not None:
                groups.append(current)
            current = {"index": int(header.group(1))}
            continue
        if current is None:
            continue

        if match := re.search(r"Block bitmap at (\d+)", line):
            current["block_bitmap_block"] = int(match.group(1))
        if match := re.search(r"Inode bitmap at (\d+)", line):
            current["inode_bitmap_block"] = int(match.group(1))
        if match := re.search(r"Inode table at (\d+)", line):
            current["inode_table_block"] = int(match.group(1))
        if match := re.search(r"(\d+) free blocks, (\d+) free inodes", line):
            current["free_blocks"] = int(match.group(1))
            current["free_inodes"] = int(match.group(2))

    if current is not None:
        groups.append(current)
    return groups


def write_file_to_image(image_path: Path, image_filename: str, host_file: Path) -> None:
    """
    Copy a host file into an unmounted ext4 image via debugfs.

    Args:
        image_path: ext4 image path.
        image_filename: Destination path inside the image.
        host_file: Local file to write.
    """
    subprocess.run(
        ["debugfs", "-w", "-R", f"write {host_file} {image_filename}", str(image_path)],
        check=True,
        capture_output=True,
    )


def delete_file_from_image(image_path: Path, image_filename: str) -> None:
    """
    Delete a file from an unmounted ext4 image via debugfs.

    Args:
        image_path: ext4 image path.
        image_filename: Path inside the image to remove.
    """
    subprocess.run(
        ["debugfs", "-w", "-R", f"rm {image_filename}", str(image_path)],
        check=True,
        capture_output=True,
    )


def tools_available() -> bool:
    """Return True when mkfs.ext4, debugfs, and dumpe2fs are available."""
    import shutil

    return (
        shutil.which("mkfs.ext4") is not None
        and shutil.which("debugfs") is not None
        and shutil.which("dumpe2fs") is not None
    )
