"""
Inter-process control channel for single-instance applications.
"""

import os
import socket
import threading
from typing import Callable, Optional

from config.storage_paths import CONTROL_SOCKET_FILENAME, LOCK_DIR

SOCKET_FILE = os.path.join(LOCK_DIR, CONTROL_SOCKET_FILENAME)
SHOW_COMMAND = b"SHOW"


class InstanceControlServer:
    """
    Unix socket server that receives commands for the running application.

    A second launch can send ``SHOW`` to raise the existing main window.
    """

    def __init__(self, on_show: Callable[[], None]) -> None:
        """
        Initialize the control server.

        Args:
            on_show: Callback invoked when another instance requests focus.
        """
        self._on_show = on_show
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._server_socket: Optional[socket.socket] = None

    def start(self) -> None:
        """Start listening for control commands in a background thread."""
        if self._thread is not None:
            return

        self._thread = threading.Thread(target=self._run, name="byteback-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the control server and remove the socket file."""
        self._stop_event.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
            self._server_socket = None

        if os.path.exists(SOCKET_FILE):
            try:
                os.unlink(SOCKET_FILE)
            except OSError:
                pass

    def _run(self) -> None:
        """Accept and dispatch control commands until stopped."""
        os.makedirs(LOCK_DIR, exist_ok=True)
        if os.path.exists(SOCKET_FILE):
            try:
                os.unlink(SOCKET_FILE)
            except OSError:
                return

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket = server
        try:
            server.bind(SOCKET_FILE)
            server.listen(1)
            server.settimeout(0.5)
        except OSError:
            server.close()
            self._server_socket = None
            return

        while not self._stop_event.is_set():
            try:
                connection, _address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                payload = connection.recv(32)
            except OSError:
                connection.close()
                continue

            connection.close()
            if payload.strip() == SHOW_COMMAND:
                self._on_show()

        try:
            server.close()
        except OSError:
            pass
        self._server_socket = None


def request_show_existing_instance() -> bool:
    """
    Ask the running application instance to show its main window.

    Returns:
        True when a running instance accepted the request.
    """
    if not os.path.exists(SOCKET_FILE):
        return False

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(1.0)
        client.connect(SOCKET_FILE)
        client.sendall(SHOW_COMMAND)
    except OSError:
        return False
    finally:
        try:
            client.close()
        except OSError:
            pass

    return True
