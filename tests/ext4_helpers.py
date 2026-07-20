"""
Helpers for creating ext4 test images in unit tests.
"""

import subprocess
from pathlib import Path


def create_ext4_image(path: Path, size_mb: int = 32) -> None:
    """
    Create an empty ext4 image file.

    Args:
        path: Output image path.
        size_mb: Image size in megabytes.
    """
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={path}", "bs=1M", f"count={size_mb}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["mkfs.ext4", "-F", str(path)], check=True, capture_output=True)


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
    """Return True when mkfs.ext4 and debugfs are available."""
    import shutil

    return shutil.which("mkfs.ext4") is not None and shutil.which("debugfs") is not None
