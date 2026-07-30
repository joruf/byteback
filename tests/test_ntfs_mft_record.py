"""
Unit tests for NTFS MFT record parsing logic not already exercised by the
mkntfs/ntfscp-backed integration tests (e.g. ntfscp never creates a DOS 8.3
short-name attribute alongside a Win32 long name, so that preference logic
needs its own synthetic coverage).
"""

from services.filesystems.ntfs.binary import ATTR_FILE_NAME, MFT_RECORD_IN_USE, MFT_RECORD_IS_DIRECTORY
from services.filesystems.ntfs.mft_record import MftAttribute, MftRecord


def _make_file_name_content(name: str, name_type: int) -> bytes:
    """Build the resident content of a $FILE_NAME attribute for tests."""
    name_bytes = name.encode("utf-16-le")
    content = bytearray(0x42 + len(name_bytes))
    content[0x40] = len(name)
    content[0x41] = name_type
    content[0x42 : 0x42 + len(name_bytes)] = name_bytes
    return bytes(content)


def _file_name_attribute(name: str, name_type: int) -> MftAttribute:
    return MftAttribute(
        attribute_type=ATTR_FILE_NAME,
        is_non_resident=False,
        resident_content=_make_file_name_content(name, name_type),
        data_runs=[],
        real_size=0,
    )


class TestMftRecordFlags:
    """Tests for the InUse/Directory flag properties."""

    def test_in_use_record_is_not_deleted(self):
        record = MftRecord(record_number=5, flags=MFT_RECORD_IN_USE, attributes=[])

        assert record.is_in_use is True
        assert record.is_deleted is False

    def test_cleared_in_use_bit_means_deleted(self):
        record = MftRecord(record_number=5, flags=0x0000, attributes=[])

        assert record.is_in_use is False
        assert record.is_deleted is True

    def test_directory_flag_is_detected(self):
        record = MftRecord(
            record_number=5, flags=MFT_RECORD_IN_USE | MFT_RECORD_IS_DIRECTORY, attributes=[]
        )

        assert record.is_directory is True


class TestGetFileName:
    """Tests for selecting the best filename among possibly several $FILE_NAME attributes."""

    def test_prefers_win32_name_over_dos_short_name(self):
        dos_attr = _file_name_attribute("SECRET~1.TXT", name_type=2)
        win32_attr = _file_name_attribute("secret original name.txt", name_type=1)
        record = MftRecord(record_number=10, flags=MFT_RECORD_IN_USE, attributes=[dos_attr, win32_attr])

        assert record.get_file_name() == "secret original name.txt"

    def test_falls_back_to_dos_only_name_when_thats_all_there_is(self):
        dos_attr = _file_name_attribute("ONLY.TXT", name_type=2)
        record = MftRecord(record_number=11, flags=MFT_RECORD_IN_USE, attributes=[dos_attr])

        assert record.get_file_name() == "ONLY.TXT"

    def test_returns_none_when_no_file_name_attribute_present(self):
        record = MftRecord(record_number=12, flags=MFT_RECORD_IN_USE, attributes=[])

        assert record.get_file_name() is None


class TestMftRecordParseRejectsInvalidInput:
    """MftRecord.parse() must reject non-record data instead of misparsing it."""

    def test_rejects_wrong_magic(self):
        raw = b"\x00" * 1024

        assert MftRecord.parse(0, raw) is None

    def test_rejects_too_short_buffer(self):
        raw = b"FILE" + b"\x00" * 10

        assert MftRecord.parse(0, raw) is None
