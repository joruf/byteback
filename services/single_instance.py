"""
Ensure only one ByteBack instance runs at a time.
"""

import fcntl
import os
import tkinter as tk
from tkinter import messagebox
from typing import IO, Optional, Tuple

from config import APP_NAME
from config.storage_paths import INSTANCE_LOCK_FILENAME, LOCK_DIR
from services.instance_ipc import request_show_existing_instance
from ui.window_icon import apply_window_icon

LOCK_FILE = os.path.join(LOCK_DIR, INSTANCE_LOCK_FILENAME)


class SingleInstanceGuard:
    """
    Acquire and hold an exclusive lock for the lifetime of the application.
    """

    def __init__(self) -> None:
        """Initialize an unlocked guard."""
        self._lock_handle: Optional[IO[str]] = None

    def acquire(self) -> bool:
        """
        Try to acquire the single-instance lock.

        Returns:
            True when this process is the only running instance.
        """
        os.makedirs(LOCK_DIR, exist_ok=True)

        handle = open(LOCK_FILE, "w", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False

        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._lock_handle = handle
        return True

    def release(self) -> None:
        """Release the single-instance lock."""
        if self._lock_handle is None:
            return

        try:
            fcntl.flock(self._lock_handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

        try:
            self._lock_handle.close()
        except OSError:
            pass

        self._lock_handle = None


def is_instance_running() -> bool:
    """
    Check whether another ByteBack process already holds the instance lock.

    Returns:
        True when another instance is active.
    """
    os.makedirs(LOCK_DIR, exist_ok=True)

    handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        handle.close()


def show_already_running_message(focused_existing: bool = False) -> None:
    """
    Inform the user that another instance is already active.

    Args:
        focused_existing: True when the existing window was brought to the front.
    """
    root = tk.Tk()
    root.withdraw()
    apply_window_icon(root)

    if focused_existing:
        message = (
            f"{APP_NAME} is already running.\n\n"
            "The existing window has been brought to the front."
        )
        messagebox.showinfo("Already Running", message, parent=root)
    else:
        message = (
            f"{APP_NAME} is already running.\n\n"
            "Only one instance of the application can be open at a time."
        )
        messagebox.showerror("Already Running", message, parent=root)

    root.destroy()


def enforce_single_instance() -> Tuple[bool, SingleInstanceGuard]:
    """
    Block startup when another instance is already running.

    Returns:
        Tuple of (may_continue, lock_guard).
    """
    guard = SingleInstanceGuard()
    if guard.acquire():
        return True, guard

    focused_existing = request_show_existing_instance()
    show_already_running_message(focused_existing=focused_existing)
    return False, guard
