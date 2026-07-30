"""
Unit tests for FileCarver signature detection.
"""

import os
import time

from models.storage_target import StorageTarget, TargetType
from services.carving.file_carver import FileCarver as CarvingFileCarver
from services.file_carver import FileCarver
from utils.device_io import read_with_timeout as real_read_with_timeout


class HangingOnceDevice:
    """
    Fake block device: the read starting at ``hang_at_offset`` blocks for
    ``hang_seconds`` before failing; every other read returns real data.

    Used to simulate a failing/hung drive without waiting for a real one.
    """

    def __init__(self, data: bytes, hang_at_offset: int, hang_seconds: float = 5.0) -> None:
        self._data = data
        self._position = 0
        self._hang_at_offset = hang_at_offset
        self._hang_seconds = hang_seconds
        self.hang_triggered = False

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._position = offset
        elif whence == 1:
            self._position += offset
        else:
            self._position = len(self._data) + offset
        return self._position

    def read(self, size: int = -1) -> bytes:
        if self._position == self._hang_at_offset:
            self.hang_triggered = True
            time.sleep(self._hang_seconds)
            raise OSError("simulated device hang resolved as an I/O error")
        end = self._position + size if size >= 0 else len(self._data)
        chunk = self._data[self._position : end]
        self._position += len(chunk)
        return chunk

    def close(self) -> None:
        pass

    def __enter__(self) -> "HangingOnceDevice":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


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

    def test_carve_range_skips_past_hanging_read_instead_of_blocking(self, tmp_path, monkeypatch):
        """
        A read that hangs on one chunk must not block carve_range (or Cancel)
        forever — it should be skipped, with scanning continuing afterward.
        """
        chunk_size = 16
        monkeypatch.setattr("services.carving.file_carver.RAW_READ_CHUNK_SIZE", chunk_size)

        def fast_read_with_timeout(handle, size, timeout=0.05):
            return real_read_with_timeout(handle, size, timeout=timeout)

        monkeypatch.setattr("services.carving.file_carver.read_with_timeout", fast_read_with_timeout)

        jpeg_data = b"\xff\xd8\xff\xe0" + b"\x00" * 12 + b"\xff\xd9"  # 18 bytes, placed after the hung chunk
        buffer = (b"\x00" * chunk_size) * 3 + jpeg_data + (b"\x00" * chunk_size)
        hang_at_offset = chunk_size * 2  # third (all-zero) chunk read hangs; the JPEG lives later
        fake_device = HangingOnceDevice(buffer, hang_at_offset=hang_at_offset, hang_seconds=5.0)

        monkeypatch.setattr("services.carving.file_carver.open_device", lambda _path: fake_device)

        target = StorageTarget(
            target_id="hang_test",
            name="hang_test",
            device_path="/fake/hanging-device",
            target_type=TargetType.IMAGE,
            size_bytes=len(buffer),
        )

        carver = CarvingFileCarver(preview_dir=str(tmp_path))
        started = time.monotonic()
        entries, processed = carver.carve_range(
            target=target,
            start_offset=0,
            size_bytes=len(buffer),
            source_target_id="hang_test",
        )
        elapsed = time.monotonic() - started

        assert fake_device.hang_triggered is True
        assert elapsed < 2.0, "a single hung chunk must not block the whole carve"
        assert processed == len(buffer), "scanning must continue past the hung chunk to the end"
        assert len(entries) == 1
        assert entries[0].extension == ".jpg"
