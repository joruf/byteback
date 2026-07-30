"""
Unit tests for ScanWorker lifecycle and resume guards.
"""

import time
from unittest.mock import patch

from config.scan_settings import SCAN_MODE_DEEP_CARVE, SCAN_MODE_FILESYSTEM
from models.storage_target import StorageTarget, TargetType
from services.scan_worker import ScanWorker


class TestScanWorker:
    """Tests for scan worker start/resume behaviour."""

    def _partition(self, mountpoint=None):
        return StorageTarget(
            target_id="part_sda1",
            name="sda1",
            device_path="/dev/sda1",
            target_type=TargetType.PARTITION,
            size_bytes=1024,
            mountpoint=mountpoint,
        )

    def test_validate_resume_target_detects_mismatch(self, scan_state_dir):
        """Resume is blocked when target_id does not match saved state."""
        worker = ScanWorker()
        worker._state_manager.save(
            target_id="part_other",
            scan_mode="filesystem",
            bytes_processed=0,
            bytes_total=100,
            filesystem_queue=[],
            carve_offset=0,
            entries=[],
        )
        target = self._partition(mountpoint="/mnt")

        mismatch = worker._validate_resume_target(target)

        assert mismatch is not None
        assert "part_other" in mismatch

    def test_start_returns_error_on_target_mismatch(self, scan_state_dir):
        """start() reports mismatch instead of launching a wrong resume."""
        worker = ScanWorker()
        worker._state_manager.save(
            target_id="part_other",
            scan_mode="filesystem",
            bytes_processed=0,
            bytes_total=100,
            filesystem_queue=[],
            carve_offset=0,
            entries=[],
        )
        target = self._partition(mountpoint="/mnt")

        started, error = worker.start(target, resume=True)

        assert started is False
        assert error is not None

    @patch.object(ScanWorker, "_run_scan")
    def test_start_launches_thread_when_valid(self, _run_scan, tmp_path):
        """Valid start requests launch the worker thread."""
        worker = ScanWorker()
        target = self._partition(mountpoint=str(tmp_path))

        started, error = worker.start(
            target,
            resume=False,
            scan_strategy=SCAN_MODE_FILESYSTEM,
        )

        assert started is True
        assert error is None
        assert worker.is_running is True
        worker.cancel()
        worker.wait(timeout=1.0)

    @patch.object(ScanWorker, "_run_scan")
    def test_start_with_deep_carve_strategy(self, _run_scan, tmp_path):
        """Deep carve strategy starts successfully."""
        worker = ScanWorker()
        target = self._partition(mountpoint=str(tmp_path))

        started, error = worker.start(
            target,
            resume=False,
            scan_strategy=SCAN_MODE_DEEP_CARVE,
        )

        assert started is True
        assert error is None
        worker.cancel()
        worker.wait(timeout=1.0)

    def test_emit_progress_reuses_eta_between_intervals(self, monkeypatch):
        """ETA is recalculated only every five seconds."""
        worker = ScanWorker()
        worker._start_time = 1000.0
        worker._bytes_total = 1000
        worker._bytes_processed = 500
        worker._scan_mode = "deep_carve"
        worker._last_eta_emit = 1000.0
        worker._cached_eta_seconds = 120.0

        times = iter([1002.0, 1006.0])
        monkeypatch.setattr("services.scan_worker.time.time", lambda: next(times))

        worker._emit_progress()
        assert worker._cached_eta_seconds == 120.0

        worker._emit_progress()
        assert worker._cached_eta_seconds != 120.0
        assert worker._cached_eta_seconds == 6.0

    def test_pause_persists_a_resumable_checkpoint(self, tmp_path, scan_state_dir):
        """
        Regression test: pausing a running scan must actually persist a checkpoint.

        Previously, pause() only set a flag while every scan backend absorbed it
        with an in-place sleep loop instead of returning — so the worker thread
        never reached the code that saves state, and closing the app mid-pause
        silently lost the whole scan despite the UI claiming "state saved".

        Uses a fake executor (rather than a real directory scan) so pausing is
        deterministic instead of racing against how fast a real scan finishes.
        """
        target = self._partition(mountpoint=str(tmp_path))
        worker = ScanWorker()

        def fake_execute(
            *,
            target,
            scan_strategy,
            source_entries,
            filesystem_queue,
            carve_offset,
            ext4_inode_cursor,
            free_space_range_index,
            on_entry,
            on_progress,
            should_pause,
            should_cancel,
        ):
            for step in range(500):
                if should_cancel():
                    break
                if should_pause():
                    return (
                        "filesystem",
                        list(source_entries),
                        {
                            "bytes_processed": step,
                            "filesystem_queue": [f"/pretend/dir_{step}"],
                            "carve_offset": 0,
                            "ext4_inode_cursor": 0,
                            "free_space_range_index": 0,
                        },
                    )
                time.sleep(0.01)
            return "filesystem", list(source_entries), {
                "bytes_processed": 500,
                "filesystem_queue": [],
                "carve_offset": 0,
                "ext4_inode_cursor": 0,
                "free_space_range_index": 0,
            }

        worker._executor.execute = fake_execute

        started, error = worker.start(target, resume=False, scan_strategy=SCAN_MODE_FILESYSTEM)
        assert started is True and error is None

        time.sleep(0.05)  # let the worker thread actually enter fake_execute's loop
        worker.pause()
        worker.wait(timeout=2.0)

        assert worker.is_running is False, "pause must let the worker thread actually exit"
        assert worker.has_resumable_state is True, "pause must persist a checkpoint to disk"

        summary = worker.saved_state_summary
        assert summary is not None
        assert summary["target_id"] == target.target_id
        assert summary["scan_mode"] == "filesystem"
