"""
Tests for pkexec root helper and device I/O fallback.
"""

import base64
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from services.root_helper import RootHelper, _is_allowed_path, helper_main
from utils.device_io import RootHelperDevice, open_device, read_with_timeout


class TestRootHelperPaths:
    """Tests for helper path validation."""

    def test_allows_regular_files(self, tmp_path):
        """Existing regular files are allowed."""
        image = tmp_path / "disk.dd"
        image.write_bytes(b"data")

        assert _is_allowed_path(str(image)) is True

    def test_rejects_missing_paths(self):
        """Missing paths are rejected."""
        assert _is_allowed_path("/dev/does-not-exist-xyz") is False


class TestRootHelper:
    """Tests for helper RPC client behaviour."""

    def test_start_success(self, monkeypatch):
        """Helper startup succeeds after a ping response."""
        helper = RootHelper()
        process = MagicMock()
        process.poll.return_value = None
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        process.stdout.readline.return_value = json.dumps({"status": "ok", "data": True}) + "\n"

        monkeypatch.setattr("services.root_helper.shutil.which", lambda _name: "/usr/bin/pkexec")
        monkeypatch.setattr("services.root_helper.subprocess.Popen", lambda *_args, **_kwargs: process)

        assert helper.start() is True
        assert helper.is_running() is True

    def test_read_decodes_base64_payload(self, monkeypatch):
        """Read RPC responses are decoded into bytes."""
        helper = RootHelper()
        payload = base64.b64encode(b"hello").decode("ascii")
        process = MagicMock()
        process.poll.return_value = None
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        process.stdout.readline.return_value = json.dumps({"status": "ok", "data": payload}) + "\n"
        helper._proc = process

        assert helper.read("/dev/sda", 0, 5) == b"hello"


class TestHelperMain:
    """Tests for helper-side RPC handling."""

    def test_helper_read_action(self, tmp_path, monkeypatch):
        """Helper read action returns base64-encoded bytes."""
        image = tmp_path / "disk.dd"
        image.write_bytes(b"abcdef")

        output = []

        def fake_print(*args, **kwargs) -> None:
            if args:
                output.append(args[0])

        monkeypatch.setattr("builtins.print", fake_print)
        lines = iter(
            [
                json.dumps(
                    {
                        "action": "read",
                        "path": str(image),
                        "offset": 1,
                        "size": 3,
                    }
                )
                + "\n",
                "",
            ]
        )
        monkeypatch.setattr("services.root_helper.sys.stdin.readline", lambda: next(lines))

        helper_main()

        response = json.loads(output[0])
        assert response["status"] == "ok"
        assert base64.b64decode(response["data"]) == b"bcd"


class TestDeviceIo:
    """Tests for device open fallback."""

    def test_open_device_uses_helper_when_direct_open_fails(self, tmp_path, monkeypatch):
        """Unreadable local paths fall back to the root helper device wrapper."""
        image = tmp_path / "disk.dd"
        image.write_bytes(b"x" * 512)

        helper = MagicMock()
        helper.is_running.return_value = True
        helper.probe.return_value = True
        helper.size.return_value = 512
        helper.read.return_value = b"abc"

        monkeypatch.setattr("utils.device_io.ROOT_HELPER", helper)
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("permission denied")))

        with open_device(str(image)) as device:
            assert isinstance(device, RootHelperDevice)
            assert device.read(3) == b"abc"

    def test_open_device_raises_when_unavailable(self, monkeypatch):
        """Missing helper access raises OSError."""
        helper = MagicMock()
        helper.is_running.return_value = False
        monkeypatch.setattr("utils.device_io.ROOT_HELPER", helper)
        monkeypatch.setattr("utils.device_io.os.path.exists", lambda _path: True)
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("permission denied")))

        with pytest.raises(OSError):
            open_device("/dev/sda")


class FakeSlowHandle:
    """Fake device handle whose read() blocks, to test read_with_timeout()."""

    def __init__(self, sleep_seconds: float, result: bytes = b"", to_raise: Exception = None) -> None:
        self._sleep_seconds = sleep_seconds
        self._result = result
        self._to_raise = to_raise

    def read(self, _size: int) -> bytes:
        time.sleep(self._sleep_seconds)
        if self._to_raise:
            raise self._to_raise
        return self._result


class TestReadWithTimeout:
    """
    Tests for the read-hang guard used throughout scanning/imaging.

    A failing or hung drive must never block a caller forever — this is what
    makes Cancel responsive even when a single read stalls.
    """

    def test_fast_read_returns_data(self, tmp_path):
        """A normal, quick read returns the requested bytes unchanged."""
        image = tmp_path / "disk.dd"
        image.write_bytes(b"abcdef")
        with open(image, "rb") as handle:
            assert read_with_timeout(handle, 6) == b"abcdef"

    def test_hanging_read_raises_timeout_promptly(self):
        """A read that never returns in time raises TimeoutError, not hang forever."""
        handle = FakeSlowHandle(sleep_seconds=5.0)
        started = time.monotonic()

        with pytest.raises(TimeoutError):
            read_with_timeout(handle, 512, timeout=0.05)

        elapsed = time.monotonic() - started
        assert elapsed < 2.0, "caller must regain control near the configured timeout, not wait for the read"

    def test_read_error_propagates_unchanged(self):
        """A real read error (e.g. a bad sector) is not masked as a timeout."""
        handle = FakeSlowHandle(sleep_seconds=0.0, to_raise=OSError("I/O error"))

        with pytest.raises(OSError, match="I/O error"):
            read_with_timeout(handle, 512, timeout=1.0)
