"""
Integration tests for ext4 deleted inode recovery.
"""

import pytest

from models.storage_target import StorageTarget, TargetType
from services.filesystems.ext4.deleted_scanner import Ext4DeletedScanner
from tests.ext4_helpers import (
    create_ext4_image,
    delete_file_from_image,
    tools_available,
    write_file_to_image,
)


@pytest.mark.skipif(not tools_available(), reason="mkfs.ext4/debugfs not available")
class TestExt4DeletedScanner:
    """Tests for deleted inode scanning."""

    def test_recovers_deleted_file_from_image(self, tmp_path):
        """Deleted file content is recovered from ext4 inode metadata."""
        image = tmp_path / "deleted.ext4"
        host_file = tmp_path / "secret.txt"
        host_file.write_text("recover this deleted file", encoding="utf-8")

        create_ext4_image(image, size_mb=32)
        write_file_to_image(image, "secret.txt", host_file)
        delete_file_from_image(image, "secret.txt")

        target = StorageTarget(
            target_id="img_test",
            name="deleted.ext4",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ext4",
        )
        scanner = Ext4DeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, _final_inode = scanner.scan(target=target, source_target_id="img_test")

        assert len(entries) >= 1
        recovered = entries[0]
        assert recovered.entry_type.value == "deleted"
        assert recovered.preview_path
        assert "recover this deleted file" in open(recovered.preview_path, encoding="utf-8").read()

    def test_supports_ext4_target(self, tmp_path):
        """supports_target detects ext4 volumes."""
        image = tmp_path / "vol.ext4"
        create_ext4_image(image, size_mb=8)
        target = StorageTarget(
            target_id="t1",
            name="vol",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ext4",
        )

        assert Ext4DeletedScanner.supports_target(target) is True
