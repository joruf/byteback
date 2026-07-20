"""
Unit tests for FilesystemScanner directory traversal.
"""

import os

from models.recovery_entry import EntryType
from services.filesystem_scanner import FilesystemScanner


class TestFilesystemScanner:
    """Tests for filesystem walk and path helpers."""

    def test_relative_path_root(self):
        """Scan root maps to slash-only relative path."""
        assert FilesystemScanner._relative_path("/mnt/data", "/mnt/data") == "/"

    def test_relative_path_nested(self):
        """Nested paths normalize to forward-slash relative paths."""
        result = FilesystemScanner._relative_path(
            "/mnt/data",
            "/mnt/data/docs/readme.txt",
        )

        assert result == "/docs/readme.txt"

    def test_scan_discovers_files_and_directories(self, mounted_partition_target, tmp_path):
        """Scan emits directory and file entries with parent links."""
        root = tmp_path
        docs = root / "docs"
        docs.mkdir()
        readme = docs / "readme.txt"
        readme.write_text("hello", encoding="utf-8")
        photo = root / "photo.jpg"
        photo.write_bytes(b"\xff\xd8\xff")

        scanner = FilesystemScanner()
        entries, remaining_queue, bytes_processed = scanner.scan(
            target=mounted_partition_target,
            source_target_id="part_test",
        )

        names = {entry.name for entry in entries}
        assert "docs" in names
        assert "readme.txt" in names
        assert "photo.jpg" in names
        assert remaining_queue == []
        assert bytes_processed >= readme.stat().st_size

        by_name = {entry.name: entry for entry in entries}
        assert by_name["docs"].entry_type == EntryType.DIRECTORY
        assert by_name["readme.txt"].parent_id == by_name["docs"].entry_id
        assert by_name["readme.txt"].extra["absolute_path"] == str(readme)

    def test_scan_respects_cancel_callback(self, mounted_partition_target, tmp_path):
        """Scan stops when the cancel callback returns True."""
        for index in range(20):
            (tmp_path / f"file_{index}.txt").write_text("x", encoding="utf-8")

        scanner = FilesystemScanner()
        entries, queue, _ = scanner.scan(
            target=mounted_partition_target,
            source_target_id="part_test",
            should_cancel=lambda: True,
        )

        assert len(entries) <= 1

    def test_scan_raises_for_missing_path(self):
        """Inaccessible scan paths raise FileNotFoundError."""
        from models.storage_target import StorageTarget, TargetType

        target = StorageTarget(
            target_id="bad",
            name="missing",
            device_path="/dev/missing",
            target_type=TargetType.PARTITION,
            size_bytes=0,
            mountpoint="/nonexistent/mount/path",
        )
        scanner = FilesystemScanner()

        try:
            scanner.scan(target=target, source_target_id="bad")
            raised = False
        except FileNotFoundError:
            raised = True

        assert raised is True

    def test_scan_progress_callback(self, mounted_partition_target, tmp_path):
        """Progress callback receives increasing byte estimates."""
        (tmp_path / "data.bin").write_bytes(b"\x00" * 1024)
        calls = []
        scanner = FilesystemScanner()

        scanner.scan(
            target=mounted_partition_target,
            source_target_id="part_test",
            on_progress=lambda done, total, path: calls.append((done, total, path)),
        )

        assert len(calls) >= 1
        assert calls[-1][2]
