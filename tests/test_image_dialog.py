"""
Tests for the disk imaging dialog's window lifecycle.
"""

from ui.image_dialog import DiskImageDialog


class TestDiskImageDialogClose:
    """Closing the dialog while imaging is running must not abandon the background thread."""

    def test_wm_delete_window_is_bound(self, tk_root):
        """The OS close button must be wired to a handler, not left as Tk's bare destroy."""
        dialog = DiskImageDialog(tk_root, targets=[])
        try:
            bound = dialog.protocol("WM_DELETE_WINDOW")
            assert bound
        finally:
            dialog._running = False
            dialog.destroy()

    def test_on_cancel_does_not_destroy_while_running(self, tk_root):
        """Closing while imaging runs must cancel it, not destroy the dialog out from under it."""
        dialog = DiskImageDialog(tk_root, targets=[])
        dialog._running = True
        try:
            dialog._on_cancel()

            assert dialog.winfo_exists()
            assert dialog._cancel_event.is_set()
            assert str(dialog._cancel_button["state"]) == "disabled"
        finally:
            dialog._running = False
            dialog.destroy()

    def test_on_cancel_destroys_when_not_running(self, tk_root):
        """Closing while idle (no imaging running) just closes the dialog."""
        dialog = DiskImageDialog(tk_root, targets=[])
        dialog._running = False

        dialog._on_cancel()

        assert not dialog.winfo_exists()

    def test_check_resumable_fires_for_the_initial_default_destination(
        self, tk_root, tmp_path, monkeypatch
    ):
        """
        Regression test: the resume checkbox must be evaluated for the destination
        path pre-filled at dialog construction, not only for later manual edits —
        the write-trace used to be registered *after* that initial value was set,
        so a checkpoint matching the default destination went unnoticed.
        """
        default_dest = tmp_path / "existing_image.dd"
        checkpoint_path = tmp_path / "existing_image.dd.progress.json"
        checkpoint_path.write_text("{}", encoding="utf-8")

        monkeypatch.setattr(
            "ui.image_dialog.DiskImageRegistry.default_image_path",
            lambda _source: str(default_dest),
        )

        class FakeTarget:
            device_path = "/dev/fake"

        dialog = DiskImageDialog(tk_root, targets=[FakeTarget()])
        try:
            assert dialog._dest_var.get() == str(default_dest)
            assert dialog._resume_var.get() is True
        finally:
            dialog._running = False
            dialog.destroy()
