"""
Helpers for creating FAT32 test images in unit tests.
"""

import shutil
import subprocess
from pathlib import Path


def create_fat32_image(path: Path, size_mb: int = 64) -> None:
    """
    Create an empty FAT32 image file.

    Args:
        path: Output image path.
        size_mb: Image size in megabytes (mkfs.vfat requires a minimum size
            for FAT32; 33+ MiB is generally required).
    """
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={path}", "bs=1M", f"count={size_mb}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["mkfs.vfat", "-F", "32", str(path)], check=True, capture_output=True)


def write_file_to_image(image_path: Path, image_filename: str, host_file: Path) -> None:
    """
    Copy a host file into an unmounted FAT32 image via mtools.

    Args:
        image_path: FAT32 image path.
        image_filename: Destination path inside the image (e.g. "secret.txt"
            or "docs/secret.txt" — parent directories must already exist).
        host_file: Local file to write.
    """
    subprocess.run(
        ["mcopy", "-i", str(image_path), str(host_file), f"::{image_filename}"],
        check=True,
        capture_output=True,
    )


def make_directory(image_path: Path, dirname: str) -> None:
    """Create a directory inside an unmounted FAT32 image via mtools."""
    subprocess.run(
        ["mmd", "-i", str(image_path), f"::{dirname}"],
        check=True,
        capture_output=True,
    )


def delete_file_from_image(image_path: Path, image_filename: str) -> None:
    """
    Delete a file from an unmounted FAT32 image via mtools.

    Args:
        image_path: FAT32 image path.
        image_filename: Path inside the image to remove.
    """
    subprocess.run(
        ["mdel", "-i", str(image_path), f"::{image_filename}"],
        check=True,
        capture_output=True,
    )


def tools_available() -> bool:
    """Return True when mkfs.vfat and mtools (mcopy/mdel/mmd) are available."""
    return all(
        shutil.which(tool) is not None
        for tool in ("mkfs.vfat", "mcopy", "mdel", "mmd")
    )
