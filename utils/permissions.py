"""
Privilege and access checks for raw block-device operations.
"""

import os

from services.root_helper import ROOT_HELPER


def is_root() -> bool:
    """Return True when the process runs with effective UID 0."""
    return os.geteuid() == 0


def can_read_device(device_path: str) -> bool:
    """
    Check whether the current user can open a block device for reading.

    Args:
        device_path: Path such as ``/dev/sda``.

    Returns:
        True when the device exists and can be opened read-only.
    """
    if not os.path.exists(device_path):
        return False

    try:
        with open(device_path, "rb") as device:
            device.read(512)
        return True
    except OSError:
        pass

    if ROOT_HELPER.is_running():
        return ROOT_HELPER.probe(device_path)

    return False
