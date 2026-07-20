"""
Unit tests for DeviceScanner mountpoint parsing.
"""

from services.device_scanner import DeviceScanner


class TestDeviceScanner:
    """Tests for lsblk mountpoint resolution."""

    def test_resolve_mountpoint_from_string_field(self):
        """Legacy lsblk uses a single mountpoint string."""
        node = {"mountpoint": "/home", "mountpoints": [None]}

        assert DeviceScanner._resolve_mountpoint(node) == "/home"

    def test_resolve_mountpoint_from_array_field(self):
        """Newer lsblk versions expose mountpoints as an array."""
        node = {"mountpoint": None, "mountpoints": [None, "/media/usb"]}

        assert DeviceScanner._resolve_mountpoint(node) == "/media/usb"

    def test_resolve_mountpoint_returns_none(self):
        """Unmounted devices have no mount path."""
        node = {"mountpoint": None, "mountpoints": [None]}

        assert DeviceScanner._resolve_mountpoint(node) is None
