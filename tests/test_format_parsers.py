"""
Unit tests for format-specific carving parsers.
"""

import gzip
import io
import struct
import tarfile

from services.format_parsers import detect_file_size, validate_carved_file


def _ebml_vint_byte(value: int) -> bytes:
    """Encode a small (<=0x7E) value as a single-byte EBML vint with its marker bit."""
    return bytes([0x80 | value])


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

    def test_gif_size_with_global_color_table(self):
        """
        Regression test: the packed-fields byte must be read from offset 10 (not 9,
        which is actually the height field's high byte), and the color table must
        start after the full 13-byte logical screen descriptor (flags + background
        color index + pixel aspect ratio), not right after the flags byte.
        """
        width = struct.pack("<H", 4)
        height = struct.pack("<H", 4)
        flags = bytes([0x80])  # global color table present, size field 0 -> 2 entries (6 bytes)
        background_index = bytes([0])
        aspect_ratio = bytes([0])
        color_table = b"\x00" * 6
        trailer = bytes([0x3B])
        data = b"GIF89a" + width + height + flags + background_index + aspect_ratio + color_table + trailer

        assert detect_file_size(data, "GIF Image (89a)") == len(data)
        assert validate_carved_file(data, "GIF Image (89a)") is True

    def test_gif_size_without_global_color_table(self):
        """GIF without a global color table: blocks start immediately after the descriptor."""
        width = struct.pack("<H", 1)
        height = struct.pack("<H", 1)
        flags = bytes([0x00])  # no global color table
        background_index = bytes([0])
        aspect_ratio = bytes([0])
        trailer = bytes([0x3B])
        data = b"GIF89a" + width + height + flags + background_index + aspect_ratio + trailer

        assert detect_file_size(data, "GIF Image (89a)") == len(data)

    def test_unknown_signature_returns_zero(self):
        """Unsupported signatures return zero detected size."""
        assert detect_file_size(b"data", "Unknown Format") == 0

    def test_tiff_size_bounds_by_strip_offset_and_byte_count(self):
        """TIFF size is bounded by dereferencing StripOffsets + StripByteCounts."""
        endian = "<"
        ifd_offset = 8
        entry_count = 2
        entries_end = ifd_offset + 2 + entry_count * 12
        strip_offset = entries_end + 4  # right after the IFD (+ next-IFD-offset field)
        strip_length = 20

        strip_offsets_entry = struct.pack(endian + "HHII", 273, 4, 1, strip_offset)
        strip_counts_entry = struct.pack(endian + "HHII", 279, 4, 1, strip_length)
        ifd = (
            struct.pack(endian + "H", entry_count)
            + strip_offsets_entry
            + strip_counts_entry
            + struct.pack(endian + "I", 0)
        )
        header = b"II*\x00" + struct.pack(endian + "I", ifd_offset)
        data = header + ifd + (b"\xaa" * strip_length)

        expected = strip_offset + strip_length
        assert detect_file_size(data, "TIFF Image (LE)") == expected
        assert validate_carved_file(data, "TIFF Image (LE)") is True

    def test_webp_size_from_riff_header(self):
        """WebP size is the RIFF chunk size plus the 8-byte RIFF header."""
        payload = b"WEBPVP8 " + b"\x00" * 16
        data = b"RIFF" + struct.pack("<I", len(payload)) + payload

        assert detect_file_size(data, "WebP Image") == len(data)
        assert validate_carved_file(data, "WebP Image") is True

    def test_wav_size_from_riff_header(self):
        """WAV size is the RIFF chunk size plus the 8-byte RIFF header."""
        payload = b"WAVEfmt " + b"\x00" * 16
        data = b"RIFF" + struct.pack("<I", len(payload)) + payload

        assert detect_file_size(data, "WAV Audio") == len(data)
        assert validate_carved_file(data, "WAV Audio") is True

    def test_rtf_size_counts_balanced_braces(self):
        """RTF size ends at the outermost closing brace, ignoring escaped braces."""
        rtf = rb"{\rtf1\ansi {\b bold \{escaped\}} plain text}"
        data = rtf + b"trailing garbage that must not be included"

        assert detect_file_size(data, "RTF Document") == len(rtf)
        assert validate_carved_file(data, "RTF Document") is True

    def test_rtf_size_skips_binary_payload(self):
        """A \\binN control word's raw bytes are skipped, not brace-counted."""
        binary_blob = b"{}{}" * 4  # deliberately full of brace characters
        rtf = b"{\\rtf1\\bin" + str(len(binary_blob)).encode() + b" " + binary_blob + b"}"

        assert detect_file_size(rtf, "RTF Document") == len(rtf)

    def test_gzip_size_from_compressed_stream_length(self):
        """GZIP size is found by decompressing until the stream reports EOF."""
        payload = gzip.compress(b"hello world" * 50)
        data = payload + b"trailing garbage"

        assert detect_file_size(data, "GZIP Archive") == len(payload)
        assert validate_carved_file(data, "GZIP Archive") is True

    def test_7z_size_from_signature_header(self):
        """7z size = 32-byte signature header + next-header offset + next-header size."""
        next_header_offset = 5
        next_header_size = 3
        header = (
            b"7z\xbc\xaf\x27\x1c"
            + b"\x00\x00"
            + b"\x00\x00\x00\x00"
            + struct.pack("<Q", next_header_offset)
            + struct.pack("<Q", next_header_size)
        )
        data = header + b"\xaa" * next_header_offset + b"\xbb" * next_header_size + b"extra"

        assert detect_file_size(data, "7-Zip Archive") == 32 + next_header_offset + next_header_size
        assert validate_carved_file(data, "7-Zip Archive") is True

    def test_cfbf_size_from_fat_highest_used_sector(self):
        """Compound File size is derived from the highest sector referenced by the FAT."""
        header = bytearray(512)
        header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        struct.pack_into("<H", header, 0x1E, 9)  # sector shift -> 512-byte sectors
        struct.pack_into("<I", header, 0x2C, 1)  # one FAT sector
        struct.pack_into("<i", header, 0x4C, 0)  # DIFAT[0] = disk sector 0 holds the FAT

        fat_sector = bytearray(512)
        struct.pack_into("<I", fat_sector, 0, 0xFFFFFFFD)  # entry 0: the FAT sector itself
        struct.pack_into("<I", fat_sector, 4, 0xFFFFFFFE)  # entry 1: directory sector (end of chain)
        for i in range(2, 128):
            struct.pack_into("<I", fat_sector, i * 4, 0xFFFFFFFF)  # free

        directory_sector = bytearray(512)
        data = bytes(header) + bytes(fat_sector) + bytes(directory_sector)

        assert detect_file_size(data, "Microsoft Office Document") == len(data)
        assert validate_carved_file(data, "Microsoft Office Document") is True

    def test_mp3_frame_size_walks_consecutive_frames(self):
        """MP3 (raw frame sync) size covers exactly the valid consecutive frames."""
        frame_header = bytes([0xFF, 0xFB, 0x90, 0x00])  # MPEG1 LayerIII, 128kbps, 44100Hz
        frame_length = 417
        frame = frame_header + b"\x00" * (frame_length - len(frame_header))
        data = frame * 2 + b"\x00\x00\x00\x00"

        assert detect_file_size(data, "MP3 Audio (frame)") == frame_length * 2
        assert validate_carved_file(data, "MP3 Audio (frame)") is True

    def test_mp3_id3_size_includes_tag_and_frames(self):
        """MP3 (ID3-tagged) size includes the syncsafe tag size plus following frames."""
        tag_size = 20
        id3_header = b"ID3" + b"\x03\x00" + b"\x00" + bytes([0, 0, 0, tag_size])
        frame_header = bytes([0xFF, 0xFB, 0x90, 0x00])
        frame_length = 417
        frame = frame_header + b"\x00" * (frame_length - len(frame_header))
        data = id3_header + b"\x00" * tag_size + frame + b"\x00\x00\x00\x00"

        expected = 10 + tag_size + frame_length
        assert detect_file_size(data, "MP3 Audio (ID3)") == expected

    def test_ogg_size_ends_at_eos_page(self):
        """OGG size ends at the page marked with the end-of-stream flag."""
        payload = b"\x01" * 10
        page = (
            b"OggS"
            + b"\x00"  # version
            + b"\x04"  # header_type: EOS flag set
            + b"\x00" * 8  # granule position
            + struct.pack("<I", 1)  # serial number
            + struct.pack("<I", 0)  # page sequence number
            + b"\x00" * 4  # checksum
            + bytes([1])  # page_segments
            + bytes([len(payload)])  # segment table
            + payload
        )
        data = page + b"trailing garbage"

        assert detect_file_size(data, "OGG Audio") == len(page)
        assert validate_carved_file(data, "OGG Audio") is True

    def test_mkv_size_from_declared_segment_size(self):
        """MKV/WebM size is derived from the top-level Segment element's declared size."""
        ebml_header = b"\x1a\x45\xdf\xa3" + _ebml_vint_byte(4) + b"\x00" * 4
        segment_size = 50
        segment = b"\x18\x53\x80\x67" + _ebml_vint_byte(segment_size) + b"\x00" * segment_size
        data = ebml_header + segment

        assert detect_file_size(data, "MKV Video") == len(data)
        assert validate_carved_file(data, "MKV Video") is True

    def test_mkv_size_unknown_when_segment_size_unbounded(self):
        """An EBML 'unknown size' sentinel (all data bits set) cannot be resolved."""
        ebml_header = b"\x1a\x45\xdf\xa3" + _ebml_vint_byte(4) + b"\x00" * 4
        segment = b"\x18\x53\x80\x67" + b"\xff" + b"\x00" * 10  # 0xFF = unknown-size marker
        data = ebml_header + segment

        assert detect_file_size(data, "MKV Video") == 0

    def test_tar_size_stops_at_terminator_not_blocking_padding(self):
        """
        TAR size ends right after the two-zero-block terminator — the real archive
        content — not at tarfile's zero-padding to a full 10 KiB blocking record.
        """
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            info = tarfile.TarInfo(name="hello.txt")
            content = b"hello world"
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        data = buffer.getvalue()

        header_blocks = 512  # one 512-byte header for "hello.txt"
        content_blocks = 512  # "hello world" (11 bytes) rounded up to one 512-byte block
        terminator_blocks = 1024  # two all-zero 512-byte blocks
        expected = header_blocks + content_blocks + terminator_blocks

        assert len(data) > expected, "tarfile is expected to pad to a full blocking record"
        assert detect_file_size(data, "TAR Archive") == expected
        assert validate_carved_file(data, "TAR Archive") is True

    def test_psd_size_from_uncompressed_image_data(self):
        """PSD size is computed exactly when the final image section is uncompressed."""
        channels, height, width, depth = 1, 2, 2, 8
        header = (
            b"8BPS"
            + struct.pack(">H", 1)
            + b"\x00" * 6
            + struct.pack(">H", channels)
            + struct.pack(">I", height)
            + struct.pack(">I", width)
            + struct.pack(">H", depth)
            + struct.pack(">H", 1)
        )
        sections = struct.pack(">I", 0) * 3  # empty color-mode, resources, layer sections
        compression = struct.pack(">H", 0)
        bytes_per_row = -(-(width * depth) // 8)
        image_data = b"\xaa" * (channels * height * bytes_per_row)
        data = header + sections + compression + image_data

        assert detect_file_size(data, "PSD Image") == len(data)
        assert validate_carved_file(data, "PSD Image") is True

    def test_flac_validator_checks_streaminfo_block(self):
        """FLAC has no size formula without decoding, but gets a real structural validator."""
        streaminfo_block = bytes([0x00]) + (0, 0, 34)[2].to_bytes(3, "big") + b"\x00" * 34
        data = b"fLaC" + streaminfo_block
        assert detect_file_size(data, "FLAC Audio") == 0
        assert validate_carved_file(data, "FLAC Audio") is True
        assert validate_carved_file(b"fLaC\x01garbage", "FLAC Audio") is False

    def test_rar_validator_accepts_rar4_and_rar5_signatures(self):
        """RAR has no size formula without full block parsing, but is validated by signature."""
        assert validate_carved_file(b"Rar!\x1a\x07\x00" + b"\x00" * 10, "RAR Archive") is True
        assert validate_carved_file(b"Rar!\x1a\x07\x01\x00" + b"\x00" * 10, "RAR Archive") is True
        assert detect_file_size(b"Rar!\x1a\x07\x00", "RAR Archive") == 0

    def test_psd_size_unknown_when_compressed(self):
        """RLE/ZIP-compressed image data has no length field and cannot be sized."""
        header = (
            b"8BPS"
            + struct.pack(">H", 1)
            + b"\x00" * 6
            + struct.pack(">H", 1)
            + struct.pack(">I", 2)
            + struct.pack(">I", 2)
            + struct.pack(">H", 8)
            + struct.pack(">H", 1)
        )
        sections = struct.pack(">I", 0) * 3
        compression = struct.pack(">H", 1)  # RLE
        data = header + sections + compression + b"\xaa" * 4

        assert detect_file_size(data, "PSD Image") == 0
