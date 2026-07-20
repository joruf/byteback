"""
Unit tests for ScanStateManager persistence.
"""

import json
import os

from models.recovery_entry import EntryType, RecoveryEntry
from services.scan_state import ScanStateManager


class TestScanStateManager:
    """Tests for scan state save, load, and clear."""

    def test_save_and_load_round_trip(self, scan_state_dir):
        """Saved state can be restored with deserialized entries."""
        manager = ScanStateManager()
        entry = RecoveryEntry(
            entry_id="f1",
            name="readme.txt",
            relative_path="/readme.txt",
            entry_type=EntryType.FILE,
            size_bytes=100,
            source_target_id="part_1",
            extra={"absolute_path": "/mnt/readme.txt"},
        )

        manager.save(
            target_id="part_1",
            scan_mode="filesystem",
            bytes_processed=512,
            bytes_total=1024,
            filesystem_queue=["/mnt/data/sub"],
            carve_offset=0,
            ext4_inode_cursor=42,
            free_space_range_index=3,
            entries=[entry],
        )

        loaded = manager.load()

        assert loaded is not None
        assert loaded["target_id"] == "part_1"
        assert loaded["scan_mode"] == "filesystem"
        assert loaded["bytes_processed"] == 512
        assert loaded["filesystem_queue"] == ["/mnt/data/sub"]
        assert loaded["ext4_inode_cursor"] == 42
        assert loaded["free_space_range_index"] == 3
        assert len(loaded["entries"]) == 1
        assert loaded["entries"][0].name == "readme.txt"
        assert loaded["entries"][0].entry_type == EntryType.FILE

    def test_load_returns_none_when_missing(self, scan_state_dir):
        """Missing state file yields None."""
        manager = ScanStateManager()

        assert manager.load() is None
        assert manager.has_saved_state() is False

    def test_load_returns_none_for_corrupt_json(self, scan_state_dir):
        """Corrupted JSON is handled gracefully."""
        manager = ScanStateManager()
        with open(manager.state_path, "w", encoding="utf-8") as handle:
            handle.write("{not valid json")

        assert manager.load() is None

    def test_clear_removes_state_file(self, scan_state_dir):
        """Clear deletes the persisted state file."""
        manager = ScanStateManager()
        manager.save(
            target_id="t1",
            scan_mode="carve",
            bytes_processed=0,
            bytes_total=100,
            filesystem_queue=[],
            carve_offset=0,
            entries=[],
        )

        assert manager.has_saved_state() is True
        manager.clear()

        assert manager.has_saved_state() is False
        assert not os.path.isfile(manager.state_path)

    def test_has_saved_state_reflects_file_presence(self, scan_state_dir):
        """has_saved_state tracks file existence on disk."""
        manager = ScanStateManager()
        state_path = manager.state_path

        assert manager.has_saved_state() is False

        payload = {"target_id": "x", "entries": []}
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

        assert manager.has_saved_state() is True
