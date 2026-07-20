"""
Unit tests for RecoveryEntry, StorageTarget, and ScanProgress models.
"""

from models.recovery_entry import EntryType, RecoveryEntry
from models.scan_progress import ScanProgress
from models.storage_target import StorageTarget, TargetType


class TestRecoveryEntry:
    """Tests for RecoveryEntry serialization and properties."""

    def test_is_directory_and_is_carved(self):
        """Entry type properties reflect the entry classification."""
        directory = RecoveryEntry(
            entry_id="d1",
            name="docs",
            relative_path="/docs",
            entry_type=EntryType.DIRECTORY,
            size_bytes=0,
            source_target_id="t1",
        )
        carved = RecoveryEntry(
            entry_id="c1",
            name="carved.jpg",
            relative_path="/carved/carved.jpg",
            entry_type=EntryType.CARVED,
            size_bytes=100,
            source_target_id="t1",
            preview_path="/tmp/carved.jpg",
        )

        assert directory.is_directory is True
        assert directory.is_carved is False
        assert carved.is_carved is True
        assert carved.is_directory is False

    def test_recoverable_path_prefers_preview(self):
        """Preview path takes precedence over absolute filesystem path."""
        entry = RecoveryEntry(
            entry_id="f1",
            name="photo.jpg",
            relative_path="/photo.jpg",
            entry_type=EntryType.CARVED,
            size_bytes=512,
            source_target_id="t1",
            preview_path="/tmp/preview.jpg",
            extra={"absolute_path": "/mnt/photo.jpg"},
        )

        assert entry.recoverable_path == "/tmp/preview.jpg"

    def test_recoverable_path_uses_absolute_for_files(self):
        """Regular files resolve through the absolute_path extra field."""
        entry = RecoveryEntry(
            entry_id="f2",
            name="notes.txt",
            relative_path="/notes.txt",
            entry_type=EntryType.FILE,
            size_bytes=10,
            source_target_id="t1",
            extra={"absolute_path": "/home/user/notes.txt"},
        )

        assert entry.recoverable_path == "/home/user/notes.txt"

    def test_to_dict_from_dict_round_trip(self):
        """Serialization round-trip preserves all fields."""
        original = RecoveryEntry(
            entry_id="f3",
            name="archive.zip",
            relative_path="/archive.zip",
            entry_type=EntryType.FILE,
            size_bytes=2048,
            source_target_id="disk_1",
            mime_type="application/zip",
            extension=".zip",
            modified_time="2026-01-15 10:00:00",
            selected=True,
            parent_id="dir_1",
            children_ids=["child_1"],
            extra={"absolute_path": "/data/archive.zip"},
        )

        restored = RecoveryEntry.from_dict(original.to_dict())

        assert restored.entry_id == original.entry_id
        assert restored.name == original.name
        assert restored.entry_type == original.entry_type
        assert restored.selected is True
        assert restored.children_ids == ["child_1"]
        assert restored.extra["absolute_path"] == "/data/archive.zip"


class TestStorageTarget:
    """Tests for StorageTarget display and access helpers."""

    def test_format_size_human_readable(self):
        """Byte counts are converted to KiB/MiB labels."""
        assert StorageTarget.format_size(0) == "unknown"
        assert StorageTarget.format_size(512) == "512.0 B"
        assert StorageTarget.format_size(2048) == "2.0 KiB"
        assert StorageTarget.format_size(5 * 1024 * 1024) == "5.0 MiB"

    def test_display_name_includes_type_and_mount(self):
        """Display name shows type, size, and mountpoint when present."""
        target = StorageTarget(
            target_id="p1",
            name="nvme0n1p2",
            device_path="/dev/nvme0n1p2",
            target_type=TargetType.PARTITION,
            size_bytes=1024 ** 3,
            mountpoint="/home",
        )

        label = target.display_name

        assert "nvme0n1p2" in label
        assert "Partition" in label
        assert "→ /home" in label

    def test_scan_path_uses_mountpoint_for_mounted_partition(self):
        """Mounted partitions scan the filesystem mount path."""
        target = StorageTarget(
            target_id="p2",
            name="sda1",
            device_path="/dev/sda1",
            target_type=TargetType.PARTITION,
            size_bytes=1000,
            mountpoint="/mnt/data",
        )

        assert target.scan_path == "/mnt/data"

    def test_scan_path_uses_device_for_unmounted(self):
        """Unmounted targets scan the raw block device."""
        target = StorageTarget(
            target_id="d1",
            name="sdb",
            device_path="/dev/sdb",
            target_type=TargetType.DISK,
            size_bytes=1000,
        )

        assert target.scan_path == "/dev/sdb"

    def test_requires_root_for_unallocated_and_raw(self):
        """Raw and unallocated targets require elevated privileges."""
        unallocated = StorageTarget(
            target_id="u1",
            name="unallocated",
            device_path="/dev/sda",
            target_type=TargetType.UNALLOCATED,
            size_bytes=4096,
        )
        raw_disk = StorageTarget(
            target_id="d2",
            name="sdc",
            device_path="/dev/sdc",
            target_type=TargetType.DISK,
            size_bytes=4096,
        )
        mounted = StorageTarget(
            target_id="p3",
            name="sdc1",
            device_path="/dev/sdc1",
            target_type=TargetType.PARTITION,
            size_bytes=4096,
            mountpoint="/media/usb",
        )

        assert unallocated.requires_root is True
        assert raw_disk.requires_root is True
        assert mounted.requires_root is False


class TestScanProgress:
    """Tests for ScanProgress percentage and ETA formatting."""

    def test_percent_zero_when_total_unknown(self):
        """Progress stays at zero when total bytes are unknown."""
        progress = ScanProgress(bytes_processed=500, bytes_total=0)

        assert progress.percent == 0.0

    def test_percent_capped_at_100(self):
        """Progress never exceeds 100 percent."""
        progress = ScanProgress(bytes_processed=200, bytes_total=100)

        assert progress.percent == 100.0

    def test_percent_calculated_correctly(self):
        """Progress reflects processed-to-total ratio."""
        progress = ScanProgress(bytes_processed=250, bytes_total=1000)

        assert progress.percent == 25.0

    def test_eta_display_seconds(self):
        """Short ETAs show seconds only."""
        progress = ScanProgress(eta_seconds=45.0)

        assert progress.eta_display == "45s"

    def test_eta_display_minutes(self):
        """Medium ETAs show minutes and seconds."""
        progress = ScanProgress(eta_seconds=125.0)

        assert progress.eta_display == "2m 5s"

    def test_eta_display_hours(self):
        """Long ETAs show hours and minutes."""
        progress = ScanProgress(eta_seconds=3665.0)

        assert progress.eta_display == "1h 1m"

    def test_eta_display_dash_when_paused_or_complete(self):
        """ETA is hidden when paused, complete, or unavailable."""
        paused = ScanProgress(eta_seconds=60.0, is_paused=True)
        complete = ScanProgress(eta_seconds=60.0, is_complete=True)
        unknown = ScanProgress(eta_seconds=None)

        assert paused.eta_display == "—"
        assert complete.eta_display == "—"
        assert unknown.eta_display == "—"
