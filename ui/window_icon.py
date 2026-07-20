"""
Apply the ByteBack window icon on Linux desktops and taskbars.
"""

import os
import tkinter as tk

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_FILE = os.path.join(PROJECT_ROOT, "assets", "icons", "byteback.png")


def apply_window_icon(window: tk.Misc) -> None:
    """
    Set the window icon shown in the title bar and taskbar.

    Args:
        window: Tk root window or Toplevel dialog.
    """
    if not os.path.isfile(ICON_FILE):
        return

    try:
        icon = tk.PhotoImage(file=ICON_FILE)
    except tk.TclError:
        return

    window.iconphoto(True, icon)
    window._app_icon_image = icon
