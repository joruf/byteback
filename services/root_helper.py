"""
Single pkexec helper for privileged block-device access.

The GUI runs as the normal user. A root helper subprocess is started once at
launch and serves JSON line RPC requests for device reads.
"""

import base64
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
from typing import Any, Dict, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_SCRIPT = os.path.join(PROJECT_ROOT, "run.py")
RPC_TIMEOUT_SECONDS = 15.0

# Dedicated pool for bounding RPC reads (see _rpc()). Kept local to this module —
# utils/device_io.py already depends on this module, so importing its read-timeout
# pool here would create a circular import.
_RPC_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="byteback-helper-rpc"
)


class RootHelper:
    """
    Manage a single pkexec-launched helper for one-time authentication.
    """

    def __init__(self) -> None:
        """Initialize an inactive helper."""
        self._proc: Optional[subprocess.Popen[str]] = None
        self._rpc_lock = threading.Lock()

    def is_running(self) -> bool:
        """
        Return True when the helper process is active.

        Returns:
            True when the helper accepts RPC calls.
        """
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """
        Start the helper via pkexec and verify it with a ping.

        Returns:
            True when authentication succeeded and the helper is ready.
        """
        if shutil.which("pkexec") is None:
            return False

        helper_cmd = [
            "pkexec",
            sys.executable,
            "-u",
            RUN_SCRIPT,
            "--helper",
        ]
        try:
            self._proc = subprocess.Popen(
                helper_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            return self._rpc({"action": "ping"}) is True
        except Exception:
            self._proc = None
            return False

    def stop(self) -> None:
        """Terminate the helper if it is still running."""
        try:
            if self.is_running() and self._proc and self._proc.stdin:
                try:
                    self._rpc({"action": "quit"})
                except Exception:
                    pass
                self._proc.terminate()
        except Exception:
            pass
        finally:
            self._proc = None

    def probe(self, path: str) -> bool:
        """
        Check whether the helper can read a path.

        Args:
            path: Block device or file path.

        Returns:
            True when the helper can open the path for reading.
        """
        if not self.is_running():
            return False
        try:
            return bool(self._rpc({"action": "probe", "path": path}))
        except OSError:
            return False

    def size(self, path: str) -> int:
        """
        Return the readable size of a device or file.

        Args:
            path: Block device or file path.

        Returns:
            Size in bytes.
        """
        return int(self._rpc({"action": "size", "path": path}))

    def read(self, path: str, offset: int, size: int) -> bytes:
        """
        Read bytes from a device or file through the helper.

        Args:
            path: Block device or file path.
            offset: Start offset in bytes.
            size: Number of bytes to read.

        Returns:
            Raw bytes read from the device.
        """
        encoded = self._rpc(
            {
                "action": "read",
                "path": path,
                "offset": offset,
                "size": size,
            }
        )
        if not encoded:
            return b""
        return base64.b64decode(encoded)

    def _rpc(self, payload: Dict[str, Any]) -> Any:
        """
        Send a JSON request and return the response data.

        Serialized by ``_rpc_lock`` so concurrent callers (e.g. a scan thread and
        an imaging thread both reading through the helper) never interleave their
        request/response on the same pipe. The response read is time-bounded —
        the helper process could itself hang on a failing device, and without a
        timeout that would freeze every caller (including app shutdown) forever,
        the same class of hang ``read_with_timeout`` guards against for direct
        device reads.

        Args:
            payload: Request dictionary.

        Returns:
            Response data on success.

        Raises:
            OSError: When the helper is unavailable, doesn't respond in time, or
                reports an error — deliberately an ``OSError`` (not
                ``RuntimeError``) so it is caught by the same ``except OSError``
                handling used everywhere else a read can fail (bad sectors,
                timeouts), instead of aborting the whole scan/image.
        """
        with self._rpc_lock:
            if not self._proc or not self._proc.stdin or not self._proc.stdout:
                raise OSError("helper not running")

            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()

            future = _RPC_EXECUTOR.submit(self._proc.stdout.readline)
            try:
                line = future.result(timeout=RPC_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError as exc:
                raise OSError(
                    f"Root helper did not respond within {RPC_TIMEOUT_SECONDS}s"
                ) from exc

        if not line:
            raise OSError("no response from helper")

        response = json.loads(line)
        if response.get("status") == "ok":
            return response.get("data", True)
        raise OSError(response.get("error", "helper error"))


ROOT_HELPER = RootHelper()


def _is_allowed_path(path: str) -> bool:
    """
    Validate that a helper request targets an allowed read path.

    Args:
        path: Requested filesystem path.

    Returns:
        True when the path may be opened read-only by the helper.
    """
    if not path or not os.path.exists(path):
        return False
    if path.startswith("/dev/"):
        return True
    return os.path.isfile(path)


def helper_main() -> None:
    """Helper entrypoint executed as root under pkexec."""

    def send_ok(data: Any = True) -> None:
        print(json.dumps({"status": "ok", "data": data}), flush=True)

    def send_err(message: str) -> None:
        print(json.dumps({"status": "err", "error": message}), flush=True)

    while True:
        line = sys.stdin.readline()
        if not line:
            break

        try:
            request = json.loads(line)
            action = request.get("action")

            if action == "ping":
                send_ok(True)
            elif action == "quit":
                send_ok(True)
                break
            elif action == "probe":
                path = request.get("path", "")
                if not _is_allowed_path(path):
                    send_ok(False)
                    continue
                try:
                    with open(path, "rb") as device:
                        device.read(512)
                    send_ok(True)
                except OSError:
                    send_ok(False)
            elif action == "size":
                path = request.get("path", "")
                if not _is_allowed_path(path):
                    send_err("path not allowed")
                    continue
                with open(path, "rb") as device:
                    device.seek(0, os.SEEK_END)
                    send_ok(device.tell())
            elif action == "read":
                path = request.get("path", "")
                offset = int(request.get("offset", 0))
                size = int(request.get("size", 0))
                if size < 0:
                    send_err("invalid read size")
                    continue
                if not _is_allowed_path(path):
                    send_err("path not allowed")
                    continue
                with open(path, "rb") as device:
                    device.seek(offset)
                    data = device.read(size)
                send_ok(base64.b64encode(data).decode("ascii"))
            else:
                send_err("unknown action")
        except Exception as exc:
            send_err(str(exc))
