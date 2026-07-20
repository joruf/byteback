"""
Tests for single-instance enforcement.
"""

import fcntl
from unittest.mock import patch

from services.single_instance import SingleInstanceGuard, enforce_single_instance, is_instance_running


class TestSingleInstanceGuard:
    """Tests for exclusive process locking."""

    def test_acquire_and_release(self, tmp_path, monkeypatch):
        """A guard can acquire, release, and re-acquire the lock."""
        lock_file = tmp_path / "instance.lock"
        monkeypatch.setattr("services.single_instance.LOCK_FILE", str(lock_file))
        monkeypatch.setattr("services.single_instance.LOCK_DIR", str(tmp_path))

        guard = SingleInstanceGuard()
        assert guard.acquire() is True

        second_guard = SingleInstanceGuard()
        assert second_guard.acquire() is False

        guard.release()
        assert second_guard.acquire() is True

    def test_enforce_allows_first_instance(self, tmp_path, monkeypatch):
        """The first process may continue startup."""
        monkeypatch.setattr("services.single_instance.LOCK_FILE", str(tmp_path / "instance.lock"))
        monkeypatch.setattr("services.single_instance.LOCK_DIR", str(tmp_path))

        may_continue, guard = enforce_single_instance()
        assert may_continue is True
        assert guard._lock_handle is not None
        guard.release()

    def test_enforce_blocks_second_instance(self, tmp_path, monkeypatch):
        """A second process is blocked and attempts to focus the existing window."""
        lock_file = tmp_path / "instance.lock"
        monkeypatch.setattr("services.single_instance.LOCK_FILE", str(lock_file))
        monkeypatch.setattr("services.single_instance.LOCK_DIR", str(tmp_path))

        handle = open(lock_file, "w", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with patch("services.single_instance.request_show_existing_instance", return_value=True):
            with patch("services.single_instance.show_already_running_message") as show_message:
                may_continue, guard = enforce_single_instance()

        assert may_continue is False
        show_message.assert_called_once_with(focused_existing=True)
        handle.close()

    def test_is_instance_running_detects_active_lock(self, tmp_path, monkeypatch):
        """A held lock is reported as a running instance."""
        lock_file = tmp_path / "instance.lock"
        monkeypatch.setattr("services.single_instance.LOCK_FILE", str(lock_file))
        monkeypatch.setattr("services.single_instance.LOCK_DIR", str(tmp_path))

        handle = open(lock_file, "w", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        assert is_instance_running() is True

        handle.close()
        assert is_instance_running() is False
