"""
Shared pytest fixtures for ByteBack tests.
"""

import os

import pytest

from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget, TargetType


@pytest.fixture
def tk_root():
    """
    Provide a hidden Tk root window for UI widget tests.

    Yields:
        tkinter root window destroyed after the test.
    """
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def sample_file_entry(tmp_path):
    """
    Create a RecoveryEntry pointing at a real temporary file.

    Returns:
        Tuple of (RecoveryEntry, absolute file path).
    """
    file_path = tmp_path / "document.txt"
    file_path.write_text("hello byteback", encoding="utf-8")
    entry = RecoveryEntry(
        entry_id="file_001",
        name="document.txt",
        relative_path="/document.txt",
        entry_type=EntryType.FILE,
        size_bytes=file_path.stat().st_size,
        source_target_id="target_1",
        selected=True,
        extra={"absolute_path": str(file_path)},
    )
    return entry, str(file_path)


@pytest.fixture
def sample_directory_entry(tmp_path):
    """
    Create a directory entry with one child file.

    Returns:
        Tuple of (directory entry, child file entry, directory path).
    """
    root = tmp_path / "project"
    root.mkdir()
    child_path = root / "nested.txt"
    child_path.write_text("nested content", encoding="utf-8")

    dir_entry = RecoveryEntry(
        entry_id="dir_001",
        name="project",
        relative_path="/project",
        entry_type=EntryType.DIRECTORY,
        size_bytes=0,
        source_target_id="target_1",
        selected=True,
        children_ids=["file_002"],
        extra={"absolute_path": str(root)},
    )
    file_entry = RecoveryEntry(
        entry_id="file_002",
        name="nested.txt",
        relative_path="/project/nested.txt",
        entry_type=EntryType.FILE,
        size_bytes=child_path.stat().st_size,
        source_target_id="target_1",
        parent_id="dir_001",
        extra={"absolute_path": str(child_path)},
    )
    return dir_entry, file_entry, str(root)


@pytest.fixture
def mounted_partition_target(tmp_path):
    """
    Build a StorageTarget for a mounted partition scan.

    Returns:
        StorageTarget whose scan_path points at tmp_path.
    """
    return StorageTarget(
        target_id="part_sda1",
        name="sda1",
        device_path="/dev/sda1",
        target_type=TargetType.PARTITION,
        size_bytes=1024 * 1024,
        mountpoint=str(tmp_path),
        filesystem="ext4",
    )


@pytest.fixture
def scan_state_dir(tmp_path, monkeypatch):
    """
    Redirect scan state persistence to a temporary directory.

    Returns:
        Absolute path to the temporary state directory.
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr("services.scan_state.STATE_DIR", str(state_dir))
    monkeypatch.setattr("services.scan_state.STATE_FILENAME", "scan_state.json")
    return str(state_dir)
