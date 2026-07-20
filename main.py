"""
Application bootstrap: logging setup and main window launch.
"""

import logging
import sys


def configure_logging() -> None:
    """Configure root logger for console output."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _show_helper_auth_failed() -> None:
    """Inform the user that privileged helper startup failed."""
    message = (
        "Could not start the privileged helper.\n\n"
        "ByteBack requires one-time administrator authentication via pkexec."
    )

    try:
        import tkinter as tk
        from tkinter import messagebox

        from config import APP_NAME
        from ui.window_icon import apply_window_icon

        root = tk.Tk()
        root.withdraw()
        apply_window_icon(root)
        messagebox.showerror(APP_NAME, message, parent=root)
        root.destroy()
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    """
    Entry point for ByteBack.

    Returns:
        Process exit code (0 on success).
    """
    configure_logging()

    try:
        from services.instance_ipc import request_show_existing_instance
        from services.root_helper import ROOT_HELPER
        from services.single_instance import (
            enforce_single_instance,
            is_instance_running,
            show_already_running_message,
        )
        from ui.main_window import MainWindow

        if is_instance_running():
            focused_existing = request_show_existing_instance()
            show_already_running_message(focused_existing=focused_existing)
            return 1

        if not ROOT_HELPER.start():
            _show_helper_auth_failed()
            return 1

        try:
            may_continue, instance_guard = enforce_single_instance()
            if not may_continue:
                return 1

            app = MainWindow()
            app.attach_instance_guard(instance_guard)
            app.run()
            instance_guard.release()
            return 0
        finally:
            ROOT_HELPER.stop()
    except Exception as exc:
        logging.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
