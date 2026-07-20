"""
Unit tests for FileCarver signature detection.
"""

import os

from services.file_carver import FileCarver


class TestFileCarver:
    """Tests for magic-byte carving helpers."""

    def test_guess_mime_known_extensions(self):
        """Common extensions map to expected MIME types."""
        assert FileCarver._guess_mime(".jpg") == "image/jpeg"
        assert FileCarver._guess_mime(".PNG") == "image/png"
        assert FileCarver._guess_mime(".pdf") == "application/pdf"
        assert FileCarver._guess_mime(".mp3") == "audio/mpeg"

    def test_guess_mime_unknown_extension(self):
        """Unknown extensions fall back to octet-stream."""
        assert FileCarver._guess_mime(".xyz") == "application/octet-stream"

    def test_extract_jpeg_with_footer(self, tmp_path):
        """JPEG header and footer produce a carved entry with preview file."""
        carver = FileCarver(preview_dir=str(tmp_path))
        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 20 + b"\xff\xd9"
        buffer = b"\x00" * 10 + jpeg_data + b"\x00" * 10

        entries, remaining = carver._extract_from_buffer(
            buffer=buffer,
            device_path="/dev/sda",
            base_offset=1000,
            source_target_id="target_1",
            overlap=16,
        )

        assert len(entries) == 1
        assert entries[0].entry_type.value == "carved"
        assert entries[0].mime_type == "image/jpeg"
        assert entries[0].extension == ".jpg"
        assert os.path.isfile(entries[0].preview_path)
        assert remaining != buffer

    def test_extract_png_requires_footer(self):
        """PNG without IEND footer is not extracted."""
        carver = FileCarver()
        png_header = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        buffer = png_header

        entries, _ = carver._extract_from_buffer(
            buffer=buffer,
            device_path="/dev/sda",
            base_offset=0,
            source_target_id="target_1",
            overlap=16,
        )

        assert entries == []

    def test_extract_pdf_with_eof_footer(self, tmp_path):
        """PDF documents are carved when %%EOF footer is present."""
        carver = FileCarver(preview_dir=str(tmp_path))
        pdf_data = b"%PDF-1.4\ncontent\n%%EOF"
        buffer = b"padding" + pdf_data

        entries, _ = carver._extract_from_buffer(
            buffer=buffer,
            device_path="/dev/sdb",
            base_offset=500,
            source_target_id="target_2",
            overlap=8,
        )

        assert len(entries) == 1
        assert entries[0].extension == ".pdf"
        assert entries[0].mime_type == "application/pdf"
        assert entries[0].byte_offset == 500 + len(b"padding")

    def test_extract_respects_header_offset(self, tmp_path):
        """Signatures with header_offset locate files at the correct start."""
        carver = FileCarver(preview_dir=str(tmp_path))
        mp4_data = b"\x00" * 4 + b"ftyp" + b"isom" + b"\x00" * 32
        buffer = b"\xff" * 8 + mp4_data

        entries, _ = carver._extract_from_buffer(
            buffer=buffer,
            device_path="/dev/sdc",
            base_offset=0,
            source_target_id="target_3",
            overlap=12,
        )

        assert len(entries) == 1
        assert entries[0].extension in (".mp4", ".m4v")
