"""
Tests for disk image creation and registry.
"""

import hashlib

from models.disk_image import DiskImageRecord
from services.imaging.registry import DiskImageRegistry
from services.imaging.writer import DiskImageWriter


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
