"""
Tests for results tree interactions.
"""

from unittest.mock import MagicMock

from models.recovery_entry import EntryType, RecoveryEntry
from ui.results_tree import ResultsTree


class TestResultsTreeOpen:
    """Tests for opening entries from the results tree."""

    def test_double_click_opens_preview_file(self, tk_root, monkeypatch):
        """Double-clicking a carved file invokes the open callback."""
        opened = []

        tree = ResultsTree(tk_root, on_open_entry=lambda entry: opened.append(entry.entry_id))
        entry = RecoveryEntry(
            entry_id="carved_1",
            name="carved_123.jpg",
            relative_path="/carved/carved_123.jpg",
            entry_type=EntryType.CARVED,
            size_bytes=1024,
            source_target_id="target_1",
            preview_path="/tmp/byteback_previews/carved_123.jpg",
        )
        tree.set_entries([entry])
        tree_id = tree._entry_to_tree_id["carved_1"]

        monkeypatch.setattr(tree.tree, "identify_region", lambda _x, _y: "tree")
        monkeypatch.setattr(tree.tree, "identify_column", lambda _x: "#0")
        monkeypatch.setattr(tree.tree, "identify_row", lambda _y: tree_id)

        event = MagicMock()
        event.x = 120
        event.y = 20
        tree._on_double_click(event)

        assert opened == ["carved_1"]

    def test_double_click_on_directory_does_not_open(self, tk_root, monkeypatch):
        """Directories are ignored on double-click."""
        opened = []

        tree = ResultsTree(
            tk_root,
            on_open_entry=lambda entry: opened.append(entry.entry_id),
        )
        entry = RecoveryEntry(
            entry_id="dir_1",
            name="project",
            relative_path="/project",
            entry_type=EntryType.DIRECTORY,
            size_bytes=0,
            source_target_id="target_1",
        )
        tree.set_entries([entry])
        tree_id = tree._entry_to_tree_id["dir_1"]

        monkeypatch.setattr(tree.tree, "identify_region", lambda _x, _y: "tree")
        monkeypatch.setattr(tree.tree, "identify_column", lambda _x: "#0")
        monkeypatch.setattr(tree.tree, "identify_row", lambda _y: tree_id)

        event = MagicMock()
        event.x = 120
        event.y = 20
        tree._on_double_click(event)

        assert opened == []
