"""
Tests for window icon assets and helper.
"""

import os

from ui.window_icon import ICON_FILE, apply_window_icon


class TestWindowIcon:
    """Tests for taskbar/window icon wiring."""

    def test_icon_file_exists(self):
        """The PNG icon used by Tkinter is present."""
        assert os.path.isfile(ICON_FILE)

    def test_apply_window_icon_does_not_raise(self):
        """Icon helper accepts a hidden Tk root without error."""
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        try:
            apply_window_icon(root)
        finally:
            root.destroy()
