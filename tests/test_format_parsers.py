"""
Unit tests for format-specific carving parsers.
"""

from services.format_parsers import detect_file_size, validate_carved_file


class TestFormatParsers:
    """Tests for carved file size detection and validation."""

    def test_jpeg_size_detects_eoi(self):
        """JPEG size ends at EOI marker."""
        data = b"\xff\xd8\xff\xe0\x00\x10" + b"\x00" * 14 + b"\xff\xd9"

        assert detect_file_size(data, "JPEG Image") == len(data)
        assert validate_carved_file(data, "JPEG Image") is True

    def test_png_size_requires_iend(self):
        """PNG parser walks chunks until IEND."""
        ihdr_length = b"\x00\x00\x00\x0d"
        ihdr_chunk = ihdr_length + b"IHDR" + (b"\x00" * 13) + b"\x00\x00\x00\x00"
        iend_chunk = b"\x00\x00\x00\x00IEND\xaeB`\x82"
        png = b"\x89PNG\r\n\x1a\n" + ihdr_chunk + iend_chunk

        assert detect_file_size(png, "PNG Image") == len(png)
        assert validate_carved_file(png, "PNG Image") is True

    def test_pdf_size_finds_eof(self):
        """PDF size includes trailing %%EOF."""
        pdf = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

        assert detect_file_size(pdf, "PDF Document") == len(pdf)
        assert validate_carved_file(pdf, "PDF Document") is True

    def test_zip_size_finds_eocd(self):
        """ZIP size includes end-of-central-directory record."""
        zip_data = b"PK\x03\x04" + b"\x00" * 26 + b"PK\x05\x06" + b"\x00" * 18

        assert detect_file_size(zip_data, "ZIP Archive") == len(zip_data)
        assert validate_carved_file(zip_data, "ZIP Archive") is True

    def test_unknown_signature_returns_zero(self):
        """Unsupported signatures return zero detected size."""
        assert detect_file_size(b"data", "Unknown Format") == 0
