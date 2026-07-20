"""
Unit tests for RecoveryExporter.
"""

import os
import zipfile

import pytest

from models.recovery_entry import EntryType, RecoveryEntry
from services.recovery_exporter import RecoveryExporter


class TestRecoveryExporter:
    """Tests for export and selection expansion."""

    def test_export_raises_when_nothing_selected(self):
        """Export fails when no entries are marked for recovery."""
        exporter = RecoveryExporter()
        entries = [
            RecoveryEntry(
                entry_id="f1",
                name="a.txt",
                relative_path="/a.txt",
                entry_type=EntryType.FILE,
                size_bytes=1,
                source_target_id="t1",
                selected=False,
            )
        ]

        with pytest.raises(ValueError, match="No entries selected"):
            exporter.export(entries, "/tmp/out")

    def test_expand_selection_includes_directory_children(
        self,
        sample_directory_entry,
    ):
        """Selecting a directory also exports all descendant files."""
        dir_entry, file_entry, _ = sample_directory_entry
        exporter = RecoveryExporter()

        expanded = exporter._expand_selection([dir_entry], [dir_entry, file_entry])

        ids = {entry.entry_id for entry in expanded}
        assert ids == {"dir_001", "file_002"}

    def test_export_direct_copies_files(self, sample_file_entry, tmp_path):
        """Direct export copies selected files to the destination."""
        entry, _ = sample_file_entry
        exporter = RecoveryExporter()
        dest = tmp_path / "recovery"

        result = exporter.export([entry], str(dest), use_zip=False)

        assert result == str(dest)
        assert (dest / "document.txt").read_text(encoding="utf-8") == "hello byteback"

    def test_export_zip_creates_archive(self, sample_file_entry, tmp_path):
        """ZIP export packages files into a single archive."""
        entry, source_path = sample_file_entry
        exporter = RecoveryExporter()
        dest = tmp_path / "recovery"

        zip_path = exporter.export([entry], str(dest), use_zip=True)

        assert zip_path.endswith(".zip")
        assert os.path.isfile(zip_path)
        with zipfile.ZipFile(zip_path, "r") as archive:
            names = archive.namelist()
            assert "document.txt" in names
            assert archive.read("document.txt").decode("utf-8") == "hello byteback"

    def test_export_progress_callback(self, sample_file_entry, tmp_path):
        """Progress callback receives current index and file name."""
        entry, _ = sample_file_entry
        exporter = RecoveryExporter()
        calls = []

        exporter.export(
            [entry],
            str(tmp_path / "out"),
            on_progress=lambda current, total, name: calls.append((current, total, name)),
        )

        assert calls == [(1, 1, "document.txt")]

    def test_resolve_source_path_uses_preview(self, tmp_path):
        """Carved entries resolve through their preview path."""
        preview = tmp_path / "carved.jpg"
        preview.write_bytes(b"\xff\xd8\xff")
        entry = RecoveryEntry(
            entry_id="c1",
            name="carved.jpg",
            relative_path="/carved/carved.jpg",
            entry_type=EntryType.CARVED,
            size_bytes=3,
            source_target_id="t1",
            preview_path=str(preview),
        )
        exporter = RecoveryExporter()

        assert exporter._resolve_source_path(entry) == str(preview)
