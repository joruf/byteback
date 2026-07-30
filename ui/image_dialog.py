"""
Dialog for creating a raw disk image (.dd) from a block device.
"""

import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from config.app_settings import APP_NAME
from models.storage_target import StorageTarget
from services.imaging.registry import DiskImageRegistry
from services.imaging.writer import DiskImageWriter
from ui.window_icon import apply_window_icon
from utils.permissions import can_read_device


class DiskImageDialog(tk.Toplevel):
    """
    Modal dialog to image a block device before analysis.

    Creates a raw ``.dd`` file with SHA-256 verification metadata.
    """

    def __init__(
        self,
        master: tk.Misc,
        targets: list,
        on_complete: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Args:
            master: Parent window.
            targets: Available StorageTarget list for source selection.
            on_complete: Callback invoked after a successful image creation.
        """
        super().__init__(master)
        self.title(f"{APP_NAME} – Create Disk Image")
        apply_window_icon(self)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self._targets = [target for target in targets if target.device_path.startswith("/dev/")]
        self._on_complete = on_complete
        self._writer = DiskImageWriter()
        self._registry = DiskImageRegistry()
        self._running = False
        self._cancel_event = threading.Event()

        self._source_var = tk.StringVar()
        self._dest_var = tk.StringVar()
        self._progress_var = tk.DoubleVar(value=0.0)
        self._status_var = tk.StringVar(value="Select a source device and destination.")
        self._resume_var = tk.BooleanVar(value=False)

        # Registered before _build_widgets() so the default destination it fills in
        # (which may itself already have a resumable checkpoint) is checked too,
        # not just later manual edits.
        self._dest_var.trace_add("write", lambda *_args: self._check_resumable())
        self._build_widgets()
        if self._targets:
            self._source_var.set(self._targets[0].device_path)

        # Route the window manager's close button through the same cancel logic as
        # the Cancel button: while imaging is running this must not just destroy the
        # dialog and abandon the background thread unobserved (with no way to
        # cancel it and a checkpoint save it never gets to make).
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _build_widgets(self) -> None:
        """Lay out dialog controls."""
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="Source device:").grid(row=0, column=0, sticky="w")
        source_values = [target.device_path for target in self._targets]
        ttk.Combobox(
            frame,
            textvariable=self._source_var,
            values=source_values,
            state="readonly" if source_values else "normal",
            width=42,
        ).grid(row=0, column=1, sticky="we", pady=4)

        ttk.Label(frame, text="Destination (.dd):").grid(row=1, column=0, sticky="w")
        dest_row = ttk.Frame(frame)
        dest_row.grid(row=1, column=1, sticky="we", pady=4)
        ttk.Entry(dest_row, textvariable=self._dest_var, width=34).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dest_row, text="Browse…", command=self._browse_destination).pack(side=tk.LEFT, padx=(6, 0))

        self._resume_check = ttk.Checkbutton(
            frame,
            text="Resume interrupted imaging (found saved progress for this destination)",
            variable=self._resume_var,
        )
        self._resume_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._resume_check.grid_remove()

        ttk.Progressbar(frame, variable=self._progress_var, maximum=100.0).grid(
            row=3, column=0, columnspan=2, sticky="we", pady=(8, 4)
        )
        ttk.Label(frame, textvariable=self._status_var, wraplength=420).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )

        button_row = ttk.Frame(frame)
        button_row.grid(row=5, column=0, columnspan=2, pady=(12, 0), sticky="e")
        self._start_button = ttk.Button(
            button_row,
            text="Create image",
            style="Primary.TButton",
            command=self._start_imaging,
        )
        self._start_button.pack(side=tk.LEFT, padx=(0, 6))
        self._cancel_button = ttk.Button(button_row, text="Cancel", command=self._on_cancel)
        self._cancel_button.pack(side=tk.LEFT)

        if self._targets:
            default_dest = DiskImageRegistry.default_image_path(self._targets[0].device_path)
            self._dest_var.set(default_dest)

    def _browse_destination(self) -> None:
        """Pick output path for the disk image."""
        path = filedialog.asksaveasfilename(
            title="Save disk image",
            defaultextension=".dd",
            filetypes=[("Raw disk image", "*.dd"), ("All files", "*.*")],
        )
        if path:
            self._dest_var.set(path)

    def _on_cancel(self) -> None:
        """Stop a running imaging job (progress is checkpointed for later resume), or just close."""
        if self._running:
            self._cancel_event.set()
            self._cancel_button.configure(state="disabled")
            self._status_var.set("Cancelling… progress will be saved for resume.")
        else:
            self.destroy()

    def _check_resumable(self) -> None:
        """Show the resume checkbox when a saved checkpoint exists for this destination."""
        destination = self._dest_var.get().strip()
        if destination and DiskImageWriter.has_resumable_checkpoint(destination):
            self._resume_var.set(True)
            self._resume_check.grid()
        else:
            self._resume_var.set(False)
            self._resume_check.grid_remove()

    def _start_imaging(self) -> None:
        """Validate inputs and start background imaging."""
        if self._running:
            return

        source = self._source_var.get().strip()
        destination = self._dest_var.get().strip()
        if not source or not destination:
            messagebox.showwarning(APP_NAME, "Please select source and destination.")
            return

        if not can_read_device(source):
            messagebox.showerror(
                APP_NAME,
                f"Cannot read {source}. Root privileges may be required.",
            )
            return

        self._running = True
        self._cancel_event.clear()
        self._start_button.configure(state="disabled")
        self._status_var.set("Creating disk image…")

        resume = self._resume_var.get()
        thread = threading.Thread(
            target=self._run_imaging,
            args=(source, destination, resume),
            daemon=True,
        )
        thread.start()

    def _run_imaging(self, source: str, destination: str, resume: bool) -> None:
        """Background imaging thread."""
        try:
            record, output_path = self._writer.create_image(
                source_device=source,
                destination_path=destination,
                on_progress=self._on_progress,
                should_cancel=self._cancel_event.is_set,
                resume=resume,
            )
            self._registry.register(record)
            message = f"Image created successfully.\n\n{output_path}\n\nSHA-256:\n{record.sha256}"
            if record.has_bad_sectors:
                message += (
                    f"\n\nWarning: {len(record.bad_sector_ranges)} unreadable sector range(s) "
                    "on the source were zero-filled in the image."
                )
            self.after(0, lambda: self._imaging_done(True, message))
        except Exception as exc:
            self.after(0, lambda: self._imaging_done(False, str(exc)))

    def _on_progress(self, processed: int, total: int, status: str) -> None:
        """Thread-safe progress update."""
        percent = (processed / total * 100.0) if total > 0 else 0.0
        self.after(0, lambda: self._apply_progress(percent, status))

    def _apply_progress(self, percent: float, status: str) -> None:
        """Update progress widgets on the UI thread."""
        self._progress_var.set(percent)
        self._status_var.set(status)

    def _imaging_done(self, success: bool, message: str) -> None:
        """Show result and close on success."""
        self._running = False
        self._start_button.configure(state="normal")
        self._cancel_button.configure(state="normal")
        if success:
            messagebox.showinfo(APP_NAME, message)
            if self._on_complete:
                self._on_complete()
            self.destroy()
        elif message == "Disk imaging cancelled":
            self._status_var.set("Cancelled. Progress was saved — reopen and check "
                                  "\"Resume interrupted imaging\" to continue.")
        else:
            messagebox.showerror(APP_NAME, f"Imaging failed:\n{message}")
