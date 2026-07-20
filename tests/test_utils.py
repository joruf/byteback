"""
Unit tests for file_info and permissions utilities.
"""

import os
from datetime import datetime

from utils import file_info
from utils.permissions import can_read_device, is_root


class TestFileInfo:
    """Tests for MIME detection and formatting helpers."""

    def test_human_size_formats_units(self):
        """Byte sizes are shown with appropriate units."""
        assert file_info.human_size(0) == "0 B"
        assert file_info.human_size(500) == "500.0 B"
        assert file_info.human_size(1536) == "1.5 KiB"
        assert file_info.human_size(5 * 1024 ** 2) == "5.0 MiB"

    def test_format_timestamp_local_string(self):
        """UNIX timestamps convert to a readable local datetime."""
        epoch = datetime(2026, 3, 15, 14, 30, 0).timestamp()

        formatted = file_info.format_timestamp(epoch)

        assert formatted == "2026-03-15 14:30:00"

    def test_detect_mime_type_from_extension(self, tmp_path):
        """Known file extensions resolve through mimetypes."""
        png_file = tmp_path / "image.png"
        png_file.write_bytes(b"\x89PNG")

        assert file_info.detect_mime_type(str(png_file)) == "image/png"

    def test_detect_mime_type_returns_none_for_unknown(self, tmp_path):
        """Unknown extensions without magic library return None."""
        unknown = tmp_path / "data.unknownext"
        unknown.write_bytes(b"\x00\x01\x02")

        assert file_info.detect_mime_type(str(unknown)) is None


class TestPermissions:
    """Tests for privilege and device access checks."""

    def test_is_root_reflects_euid(self, monkeypatch):
        """is_root returns True only for effective UID 0."""
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        assert is_root() is True

        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        assert is_root() is False

    def test_can_read_device_false_when_missing(self):
        """Non-existent devices are not readable."""
        assert can_read_device("/dev/nonexistent_device_xyz") is False

    def test_can_read_device_checks_access(self, tmp_path):
        """Existing readable files can be opened for recovery-style reads."""
        device = tmp_path / "fake_device"
        device.write_bytes(b"data")

        assert can_read_device(str(device)) is True
