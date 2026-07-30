"""
Unit tests for FAT32 directory entry parsing.
"""

import struct

from services.filesystems.fat32.binary import ATTR_ARCHIVE, ATTR_DIRECTORY, ATTR_LONG_NAME
from services.filesystems.fat32.directory_entry import Fat32DirEntry


def _make_entry(name8: bytes, ext3: bytes, attr: int, first_cluster: int, file_size: int) -> bytes:
    """Build one raw 32-byte FAT directory entry for tests."""
    entry = bytearray(32)
    entry[0:8] = name8.ljust(8, b" ")
    entry[8:11] = ext3.ljust(3, b" ")
    entry[11] = attr
    struct.pack_into("<H", entry, 20, first_cluster >> 16)
    struct.pack_into("<H", entry, 26, first_cluster & 0xFFFF)
    struct.pack_into("<I", entry, 28, file_size)
    return bytes(entry)


class TestFat32DirEntry:
    """Tests for parsing a single 32-byte directory entry."""

    def test_parses_regular_file_entry(self):
        raw = _make_entry(b"SECRET", b"TXT", ATTR_ARCHIVE, first_cluster=5, file_size=100)

        entry = Fat32DirEntry.parse(raw)

        assert entry.name == "SECRET.TXT"
        assert entry.is_directory is False
        assert entry.is_deleted is False
        assert entry.first_cluster == 5
        assert entry.file_size == 100

    def test_parses_directory_entry(self):
        raw = _make_entry(b"DOCS", b"", ATTR_DIRECTORY, first_cluster=8, file_size=0)

        entry = Fat32DirEntry.parse(raw)

        assert entry.is_directory is True
        assert entry.name == "DOCS"

    def test_deleted_entry_redacts_unrecoverable_first_char(self):
        raw = bytearray(_make_entry(b"SECRET", b"TXT", ATTR_ARCHIVE, first_cluster=5, file_size=100))
        raw[0] = 0xE5  # the deletion marker overwrites the real first character

        entry = Fat32DirEntry.parse(bytes(raw))

        assert entry.is_deleted is True
        assert entry.name == "_ECRET.TXT"

    def test_long_name_fragment_is_flagged_not_a_real_entry(self):
        raw = _make_entry(b"\x01A\x00B\x00\x00\x00", b"\x00\x00\x00", ATTR_LONG_NAME, 0, 0)

        entry = Fat32DirEntry.parse(raw)

        assert entry.is_long_name_part is True
        assert entry.is_directory is False

    def test_dot_and_dotdot_entries_are_detected(self):
        dot = Fat32DirEntry.parse(_make_entry(b".", b"", ATTR_DIRECTORY, 5, 0))
        dotdot = Fat32DirEntry.parse(_make_entry(b"..", b"", ATTR_DIRECTORY, 0, 0))

        assert dot.is_dot_entry is True
        assert dotdot.is_dot_entry is True
