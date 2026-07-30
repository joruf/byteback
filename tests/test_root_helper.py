"""
Tests for pkexec root helper and device I/O fallback.
"""

import base64
import json
import os
import threading
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


class SlowRpcProcess:
    """Fake helper subprocess whose stdout.readline() hangs, for RPC timeout tests."""

    def __init__(self, sleep_seconds: float) -> None:
        self.stdin = MagicMock()
        self.stdout = MagicMock()
        self.stdout.readline.side_effect = lambda: time.sleep(sleep_seconds) or ""

    def poll(self):
        return None


class RacyEchoProcess:
    """
    Fake helper subprocess that echoes back the "marker" field of whatever request
    it most recently received — used to detect cross-talk between concurrent RPC
    callers sharing the same pipe (see TestRootHelperRpcRobustness).
    """

    def __init__(self, response_delay: float) -> None:
        self._response_delay = response_delay
        self._pending = None
        self.stdin = self
        self.stdout = self

    def write(self, data: str) -> None:
        self._pending = json.loads(data)

    def flush(self) -> None:
        pass

    def readline(self) -> str:
        # Sleeping here (after the write already landed) widens the window in which
        # a second, unsynchronized caller's write could clobber self._pending before
        # this response is built — without the RPC lock, this reliably surfaces the
        # cross-talk bug instead of only occasionally reproducing it.
        time.sleep(self._response_delay)
        return json.dumps({"status": "ok", "data": self._pending["marker"]}) + "\n"

    def poll(self):
        return None


class TestRootHelperRpcRobustness:
    """
    Regression tests for the root-helper RPC channel: it must not hang the caller
    forever on an unresponsive helper, must raise OSError (not RuntimeError) so
    existing bad-sector-skipping error handling actually catches it, and must
    serialize concurrent callers instead of letting them read each other's
    responses off the same pipe.
    """

    def test_rpc_times_out_instead_of_hanging_forever(self, monkeypatch):
        monkeypatch.setattr("services.root_helper.RPC_TIMEOUT_SECONDS", 0.05)
        helper = RootHelper()
        helper._proc = SlowRpcProcess(sleep_seconds=5.0)

        started = time.monotonic()
        with pytest.raises(OSError):
            helper._rpc({"action": "ping"})
        elapsed = time.monotonic() - started

        assert elapsed < 2.0, "a hung helper must be bounded by the timeout, not the real hang duration"

    def test_rpc_error_raises_oserror(self):
        """A helper-reported error must be an OSError so existing OSError handlers catch it."""
        helper = RootHelper()
        process = MagicMock()
        process.poll.return_value = None
        process.stdin = MagicMock()
        process.stdout = MagicMock()
        process.stdout.readline.return_value = json.dumps({"status": "err", "error": "boom"}) + "\n"
        helper._proc = process

        with pytest.raises(OSError, match="boom"):
            helper._rpc({"action": "read"})

    def test_concurrent_rpc_calls_do_not_cross_talk(self):
        """
        Regression test: two threads issuing RPCs at the same time must each get
        back their own response, never the other thread's — the shared pipe has
        no per-request id, so this relies entirely on _rpc's lock serializing
        access.
        """
        helper = RootHelper()
        helper._proc = RacyEchoProcess(response_delay=0.005)

        mismatches = []
        lock = threading.Lock()

        def worker(marker: str) -> None:
            for _ in range(20):
                result = helper._rpc({"action": "echo", "marker": marker})
                if result != marker:
                    with lock:
                        mismatches.append((marker, result))

        threads = [threading.Thread(target=worker, args=(f"thread-{i}",)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)

        assert mismatches == []
