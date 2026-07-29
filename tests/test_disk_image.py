"""
Tests for disk image creation and registry.
"""

import hashlib
import os

import pytest

from models.disk_image import DiskImageRecord
from services.imaging.registry import DiskImageRegistry
from services.imaging.writer import DiskImageWriter


class FaultInjectingSource:
    """
    Wraps a real file, raising ``OSError`` for any read whose range overlaps a
    configured "bad sector" byte range — simulates a failing source device
    without needing a real bad disk.
    """

    def __init__(self, path: str, bad_ranges) -> None:
        self._file = open(path, "rb")
        self._bad_ranges = list(bad_ranges)
        self._position = 0

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        result = self._file.seek(offset, whence)
        self._position = result
        return result

    def read(self, size: int = -1) -> bytes:
        start = self._position
        end = start + size if size >= 0 else None
        for bad_start, bad_end in self._bad_ranges:
            if end is None or (start < bad_end and end > bad_start):
                raise OSError("simulated bad sector")
        data = self._file.read(size)
        self._position += len(data)
        return data

    def tell(self) -> int:
        return self._position

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> "FaultInjectingSource":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class TestDiskImageWriter:
    """Tests for raw disk image creation."""

    def test_create_image_from_file_source(self, tmp_path):
        """Image writer copies bytes and computes SHA-256."""
        source = tmp_path / "fake_device.bin"
        payload = b"disk-image-test-data" * 1000
        source.write_bytes(payload)
        destination = tmp_path / "copy.dd"

        writer = DiskImageWriter()
        record, output_path = writer.create_image(
            source_device=str(source),
            destination_path=str(destination),
        )

        assert output_path == str(destination)
        assert destination.read_bytes() == payload
        assert record.sha256 == hashlib.sha256(payload).hexdigest()
        assert record.size_bytes == len(payload)

    def test_verify_image_hash(self, tmp_path):
        """verify_image confirms matching SHA-256."""
        image = tmp_path / "verify.dd"
        data = b"verify-me"
        image.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()

        assert DiskImageWriter.verify_image(str(image), digest) is True
        assert DiskImageWriter.verify_image(str(image), "0" * 64) is False


class TestDiskImageRegistry:
    """Tests for image metadata persistence."""

    def test_register_and_list(self, tmp_path, monkeypatch):
        """Registry stores and reloads image records."""
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        monkeypatch.setattr("services.imaging.registry.IMAGE_DIR", str(image_dir))
        monkeypatch.setattr(
            "services.imaging.registry.IMAGE_REGISTRY_FILENAME",
            "registry.json",
        )

        registry = DiskImageRegistry()
        record = DiskImageRecord(
            image_id="img_test",
            file_path=str(tmp_path / "test.dd"),
            source_device="/dev/sda",
            size_bytes=1024,
            sha256="abc",
            created_at="2026-01-01 00:00:00 UTC",
            label="test.dd",
        )
        (tmp_path / "test.dd").write_bytes(b"x" * 1024)

        registry.register(record)
        loaded = registry.list_records()

        assert len(loaded) == 1
        assert loaded[0].image_id == "img_test"

    def test_as_storage_targets(self, tmp_path, monkeypatch):
        """Registered images appear as scannable targets."""
        image_dir = tmp_path / "images"
        image_dir.mkdir()
        monkeypatch.setattr("services.imaging.registry.IMAGE_DIR", str(image_dir))
        monkeypatch.setattr(
            "services.imaging.registry.IMAGE_REGISTRY_FILENAME",
            "registry.json",
        )

        image_path = tmp_path / "disk.dd"
        image_path.write_bytes(b"\x00" * 2048)
        registry = DiskImageRegistry()
        registry.register(
            DiskImageRecord(
                image_id="img1",
                file_path=str(image_path),
                source_device="/dev/sdb",
                size_bytes=2048,
                sha256="deadbeef",
                created_at="2026-01-01",
                label="disk.dd",
            )
        )

        targets = registry.as_storage_targets()
        assert len(targets) == 1
        assert targets[0].target_type.value == "image"


class TestDiskImageWriterBadSectors:
    """A single unreadable region must not abort the whole imaging job."""

    def test_bad_sector_range_is_zero_filled_and_recorded(self, tmp_path, monkeypatch):
        """Reads overlapping a simulated bad sector are zero-filled, the rest is intact."""
        payload = bytes((i % 251) for i in range(100_000))
        source_path = tmp_path / "faulty_device.bin"
        source_path.write_bytes(payload)
        destination_path = tmp_path / "faulty.dd"

        bad_ranges = [(40_000, 40_512)]
        monkeypatch.setattr(
            "services.imaging.writer.open_device",
            lambda path: FaultInjectingSource(path, bad_ranges),
        )

        writer = DiskImageWriter()
        record, _output_path = writer.create_image(
            source_device=str(source_path),
            destination_path=str(destination_path),
        )

        assert record.has_bad_sectors
        expected = bytearray(payload)
        for offset, length in record.bad_sector_ranges:
            expected[offset : offset + length] = b"\x00" * length

        assert destination_path.read_bytes() == bytes(expected)
        assert record.size_bytes == len(payload)
        assert record.sha256 == hashlib.sha256(bytes(expected)).hexdigest()
        assert not DiskImageWriter.has_resumable_checkpoint(str(destination_path))


class TestDiskImageWriterResume:
    """Imaging interrupted mid-way must be resumable without re-copying from byte zero."""

    def test_resume_continues_after_cancellation(self, tmp_path, monkeypatch):
        """A cancelled run checkpoints progress; resume=True continues and finishes correctly."""
        monkeypatch.setattr("services.imaging.writer.IMAGE_READ_CHUNK_SIZE", 1024)
        monkeypatch.setattr("services.imaging.writer.IMAGE_CHECKPOINT_INTERVAL_SECONDS", 0.0)

        payload = bytes((i % 256) for i in range(10 * 1024))
        source_path = tmp_path / "source.bin"
        source_path.write_bytes(payload)
        destination_path = tmp_path / "resume.dd"

        writer = DiskImageWriter()
        progress_calls = []

        with pytest.raises(RuntimeError, match="cancelled"):
            writer.create_image(
                source_device=str(source_path),
                destination_path=str(destination_path),
                on_progress=lambda processed, total, status: progress_calls.append(processed),
                should_cancel=lambda: len(progress_calls) >= 3,
            )

        assert DiskImageWriter.has_resumable_checkpoint(str(destination_path))
        partial_size = destination_path.stat().st_size
        assert 0 < partial_size < len(payload)

        record, output_path = writer.create_image(
            source_device=str(source_path),
            destination_path=str(destination_path),
            resume=True,
        )

        assert output_path == str(destination_path)
        assert destination_path.read_bytes() == payload
        assert record.size_bytes == len(payload)
        assert record.sha256 == hashlib.sha256(payload).hexdigest()
        assert not record.has_bad_sectors
        assert not DiskImageWriter.has_resumable_checkpoint(str(destination_path))

    def test_resume_without_checkpoint_starts_fresh(self, tmp_path):
        """resume=True with no prior checkpoint behaves like a normal fresh run."""
        payload = b"no-checkpoint-here" * 100
        source_path = tmp_path / "source.bin"
        source_path.write_bytes(payload)
        destination_path = tmp_path / "fresh.dd"

        writer = DiskImageWriter()
        record, _output_path = writer.create_image(
            source_device=str(source_path),
            destination_path=str(destination_path),
            resume=True,
        )

        assert destination_path.read_bytes() == payload
        assert record.sha256 == hashlib.sha256(payload).hexdigest()
