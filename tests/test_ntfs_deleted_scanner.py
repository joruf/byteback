"""
Integration tests for NTFS deleted-file recovery.
"""

import pytest

from models.storage_target import StorageTarget, TargetType
from services.filesystems.ntfs.deleted_scanner import NtfsDeletedScanner
from tests.ntfs_helpers import (
    create_ntfs_image,
    find_mft_record_number,
    mark_mft_record_deleted,
    tools_available,
    write_file_to_image,
)


@pytest.mark.skipif(not tools_available(), reason="mkntfs/ntfscp/ntfsinfo not available")
class TestNtfsDeletedScanner:
    """Tests for deleted-file scanning."""

    def test_recovers_small_resident_deleted_file(self, tmp_path):
        """A small deleted file (content stored resident, inline in the MFT record) is recovered."""
        image = tmp_path / "deleted.ntfs"
        host_file = tmp_path / "secret.txt"
        host_file.write_text("recover this deleted ntfs file", encoding="utf-8")

        create_ntfs_image(image, size_mb=64)
        write_file_to_image(image, "secret.txt", host_file)
        record_number = find_mft_record_number(image, "secret.txt")
        mark_mft_record_deleted(image, record_number)

        target = StorageTarget(
            target_id="img_test",
            name="deleted.ntfs",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ntfs",
        )
        scanner = NtfsDeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, _final_record = scanner.scan(target=target, source_target_id="img_test")

        matches = [e for e in entries if e.name == "secret.txt"]
        assert len(matches) == 1
        recovered = matches[0]
        assert recovered.entry_type.value == "deleted"
        assert recovered.extra["mft_record_number"] == record_number
        with open(recovered.preview_path, encoding="utf-8") as handle:
            assert handle.read() == "recover this deleted ntfs file"

    def test_recovers_large_non_resident_deleted_file(self, tmp_path):
        """A large deleted file (content stored non-resident, via data runs) is recovered."""
        image = tmp_path / "deleted_big.ntfs"
        host_file = tmp_path / "big.bin"
        payload = b"BIGDATA_" * 3000  # 24000 bytes, forces non-resident storage
        host_file.write_bytes(payload)

        create_ntfs_image(image, size_mb=64)
        write_file_to_image(image, "big.bin", host_file)
        record_number = find_mft_record_number(image, "big.bin")
        mark_mft_record_deleted(image, record_number)

        target = StorageTarget(
            target_id="img_big_test",
            name="deleted_big.ntfs",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ntfs",
        )
        scanner = NtfsDeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, _final_record = scanner.scan(target=target, source_target_id="img_big_test")

        matches = [e for e in entries if e.name == "big.bin"]
        assert len(matches) == 1
        with open(matches[0].preview_path, "rb") as handle:
            assert handle.read() == payload

    def test_scan_returns_immediately_when_paused(self, tmp_path):
        """Pause must make scan() return right away, not block until resumed."""
        image = tmp_path / "pause.ntfs"
        create_ntfs_image(image, size_mb=64)

        target = StorageTarget(
            target_id="pause_test",
            name="pause.ntfs",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ntfs",
        )
        scanner = NtfsDeletedScanner(preview_dir=str(tmp_path / "previews"))

        entries, final_record = scanner.scan(
            target=target,
            source_target_id="pause_test",
            should_pause=lambda: True,
        )

        assert entries == []
        assert final_record == 0

    def test_cancel_reports_actual_position_not_total(self, tmp_path):
        """
        Regression test (mirrors the equivalent ext4 fix): cancelling must record
        the actual MFT record reached, not the total record count — otherwise a
        resumed scan silently skips almost the entire MFT.
        """
        image = tmp_path / "cancel.ntfs"
        create_ntfs_image(image, size_mb=64)

        target = StorageTarget(
            target_id="cancel_test",
            name="cancel.ntfs",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ntfs",
        )
        scanner = NtfsDeletedScanner(preview_dir=str(tmp_path / "previews"))

        calls = []

        def cancel_after_five():
            calls.append(1)
            return len(calls) > 5

        _entries, final_record = scanner.scan(
            target=target,
            source_target_id="cancel_test",
            should_cancel=cancel_after_five,
        )

        assert final_record == 5

    def test_supports_target_rejects_non_ntfs(self, tmp_path):
        """supports_target returns False for a file that isn't an NTFS volume."""
        image = tmp_path / "not_ntfs.img"
        image.write_bytes(b"\x00" * 4096)
        target = StorageTarget(
            target_id="bad",
            name="not_ntfs",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=4096,
        )

        assert NtfsDeletedScanner.supports_target(target) is False

    def test_supports_ntfs_target(self, tmp_path):
        """supports_target detects NTFS volumes."""
        image = tmp_path / "vol.ntfs"
        create_ntfs_image(image, size_mb=64)
        target = StorageTarget(
            target_id="t1",
            name="vol",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="ntfs",
        )

        assert NtfsDeletedScanner.supports_target(target) is True
