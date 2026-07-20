"""
Main application window for ByteBack.
"""

import logging
import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from config import (
    ALL_SCAN_MODES,
    APP_NAME,
    SCAN_MODE_AUTO,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_TITLE,
)
from models.recovery_entry import RecoveryEntry
from models.scan_progress import ScanProgress
from models.storage_target import StorageTarget
from services.device_scanner import DeviceScanner
from services.imaging.registry import DiskImageRegistry
from services.recovery_exporter import RecoveryExporter
from services.scan_worker import ScanWorker
from services.scan_state import ScanStateManager
from ui.details_panel import DetailsPanel
from ui.image_dialog import DiskImageDialog
from ui.results_tree import ResultsTree
from ui.theme import configure_ui_style, style_listbox
from utils.permissions import can_read_device, is_root

logger = logging.getLogger(__name__)


class MainWindow(tk.Tk):
    """
    Primary GUI: device selection, scan control, results tree, and recovery export.
    """

    def __init__(self) -> None:
        """Initialize widgets, services, and load block devices."""
        super().__init__()
        self.title(WINDOW_TITLE)
        self.minsize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)
        self.geometry(f"{WINDOW_MIN_WIDTH}x{WINDOW_MIN_HEIGHT}")

        self._targets: List[StorageTarget] = []
        self._target_by_id: Dict[str, StorageTarget] = {}
        self._selected_target: Optional[StorageTarget] = None
        self._scan_worker = ScanWorker(
            on_progress=self._on_scan_progress,
            on_entry=self._on_scan_entry,
            on_finished=self._on_scan_finished,
        )
        self._exporter = RecoveryExporter()
        self._recovery_running = False

        self._status_var = tk.StringVar(value="Ready")
        self._progress_var = tk.DoubleVar(value=0.0)
        self._phase_var = tk.StringVar(value="Phase: idle")
        self._eta_var = tk.StringVar(value="ETA: —")
        self._found_var = tk.StringVar(value="Found: 0")
        self._pending_var = tk.StringVar(value="Pending: —")
        self._dest_var = tk.StringVar(value="")
        self._zip_var = tk.BooleanVar(value=False)
        self._scan_mode_var = tk.StringVar(value=SCAN_MODE_AUTO)
        self._filter_var = tk.StringVar(value="")
        self._state_manager = ScanStateManager()
        self._image_registry = DiskImageRegistry()
        self._theme_colors = configure_ui_style(self)

        self._build_menu()
        self._build_layout()
        self._refresh_devices()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_menu(self) -> None:
        """Create the application menu bar."""
        menu_bar = tk.Menu(self)
        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Refresh devices", command=self._refresh_devices)
        file_menu.add_command(label="Clear saved scan state", command=self._clear_saved_state)
        file_menu.add_separator()
        file_menu.add_command(label="Quit", command=self._on_close)
        menu_bar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menu_bar, tearoff=0)
        tools_menu.add_command(label="Create disk image (.dd)…", command=self._open_image_dialog)
        menu_bar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)

        self.config(menu=menu_bar)

    def _build_layout(self) -> None:
        """Compose all panels and controls."""
        paned = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Left: device list and scan controls
        left_frame = ttk.Frame(paned, padding=4)
        paned.add(left_frame, weight=1)

        device_frame = ttk.LabelFrame(left_frame, text="Storage Devices & Regions", padding=6)
        device_frame.pack(fill=tk.BOTH, expand=True)

        self._device_list = tk.Listbox(device_frame, exportselection=False, height=18)
        style_listbox(self._device_list, self._theme_colors)
        device_scroll = ttk.Scrollbar(device_frame, orient="vertical", command=self._device_list.yview)
        self._device_list.configure(yscrollcommand=device_scroll.set)
        self._device_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        device_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._device_list.bind("<<ListboxSelect>>", self._on_device_selected)

        ttk.Button(left_frame, text="Refresh", command=self._refresh_devices).pack(fill=tk.X, pady=(6, 0))

        control_frame = ttk.LabelFrame(left_frame, text="Scan Control", padding=6)
        control_frame.pack(fill=tk.X, pady=(8, 0))

        mode_row = ttk.Frame(control_frame)
        mode_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(mode_row, text="Scan mode:", style="Muted.TLabel").pack(side=tk.LEFT)
        self._scan_mode_combo = ttk.Combobox(
            mode_row,
            textvariable=self._scan_mode_var,
            state="readonly",
            values=ALL_SCAN_MODES,
            width=16,
        )
        self._scan_mode_combo.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(6, 0))

        self._resume_hint = ttk.Label(
            control_frame,
            text="",
            style="Muted.TLabel",
            wraplength=260,
            justify="left",
        )
        self._resume_hint.pack(fill=tk.X, pady=(0, 4))

        self._start_button = ttk.Button(
            control_frame,
            text="Start scan",
            style="Primary.TButton",
            command=self._start_scan,
        )
        self._start_button.pack(fill=tk.X, pady=2)

        self._pause_button = ttk.Button(
            control_frame,
            text="Pause",
            command=self._toggle_pause,
            state="disabled",
        )
        self._pause_button.pack(fill=tk.X, pady=2)

        self._cancel_button = ttk.Button(
            control_frame,
            text="Cancel",
            command=self._cancel_scan,
            state="disabled",
        )
        self._cancel_button.pack(fill=tk.X, pady=2)

        # Center: results tree
        center_frame = ttk.Frame(paned, padding=4)
        paned.add(center_frame, weight=3)

        results_label = ttk.Label(
            center_frame,
            text="Recovered Items (check to select for export)",
            style="Muted.TLabel",
        )
        results_label.pack(anchor="w")

        filter_row = ttk.Frame(center_frame)
        filter_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(filter_row, text="Filter:", style="Muted.TLabel").pack(side=tk.LEFT)
        filter_entry = ttk.Entry(filter_row, textvariable=self._filter_var)
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self._filter_var.trace_add("write", lambda *_args: self._apply_results_filter())

        self._results_tree = ResultsTree(center_frame, on_select_entry=self._on_entry_selected)
        self._results_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        # Right: details
        right_frame = ttk.Frame(paned, padding=4)
        paned.add(right_frame, weight=1)

        self._details = DetailsPanel(right_frame, on_view_file=self._view_file)
        self._details.pack(fill=tk.BOTH, expand=True)

        # Bottom: progress and recovery
        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.pack(fill=tk.X)

        progress_frame = ttk.LabelFrame(bottom, text="Scan Progress", padding=6)
        progress_frame.pack(fill=tk.X)

        self._progress_bar = ttk.Progressbar(
            progress_frame,
            variable=self._progress_var,
            maximum=100.0,
            mode="determinate",
        )
        self._progress_bar.pack(fill=tk.X)

        info_row = ttk.Frame(progress_frame)
        info_row.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(info_row, textvariable=self._status_var).pack(side=tk.LEFT)
        ttk.Label(info_row, textvariable=self._phase_var).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(info_row, textvariable=self._eta_var).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(info_row, textvariable=self._found_var).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(info_row, textvariable=self._pending_var).pack(side=tk.LEFT, padx=(12, 0))

        recovery_frame = ttk.LabelFrame(bottom, text="Recovery Destination", padding=6)
        recovery_frame.pack(fill=tk.X, pady=(8, 0))

        dest_row = ttk.Frame(recovery_frame)
        dest_row.pack(fill=tk.X)
        ttk.Entry(dest_row, textvariable=self._dest_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(dest_row, text="Browse…", command=self._browse_destination).pack(side=tk.LEFT, padx=(6, 0))

        options_row = ttk.Frame(recovery_frame)
        options_row.pack(fill=tk.X, pady=(6, 0))
        ttk.Checkbutton(
            options_row,
            text="Package as ZIP archive",
            variable=self._zip_var,
        ).pack(side=tk.LEFT)

        self._recover_button = ttk.Button(
            recovery_frame,
            text="Recover / Restore data",
            style="Success.TButton",
            command=self._start_recovery,
            state="disabled",
        )
        self._recover_button.pack(fill=tk.X, pady=(8, 0))

    def _refresh_devices(self) -> None:
        """Reload disks, partitions, disk images, and unallocated regions."""
        scanner = DeviceScanner()
        self._targets = scanner.scan_all()
        image_targets = self._image_registry.as_storage_targets()
        self._targets = image_targets + self._targets
        self._target_by_id = {target.target_id: target for target in self._targets}

        self._device_list.delete(0, tk.END)
        for target in self._targets:
            self._device_list.insert(tk.END, target.display_name)

        root_hint = " (root privileges may be required for raw devices)" if not is_root() else ""
        self._status_var.set(f"Found {len(self._targets)} storage targets.{root_hint}")
        self._update_resume_hint()

    def _update_resume_hint(self) -> None:
        """Show whether a paused scan can be resumed."""
        summary = self._scan_worker.saved_state_summary
        if not summary:
            self._resume_hint.configure(text="")
            return

        target_id = summary.get("target_id", "unknown")
        entry_count = summary.get("entry_count", 0)
        scan_mode = summary.get("scan_mode", "unknown")
        self._resume_hint.configure(
            text=(
                f"Paused scan available: {entry_count} items, mode={scan_mode}, "
                f"target={target_id}. Select the matching device and start/resume."
            )
        )

    def _clear_saved_state(self) -> None:
        """Remove persisted scan checkpoint."""
        if not self._state_manager.has_saved_state():
            messagebox.showinfo(APP_NAME, "No saved scan state found.")
            return
        self._state_manager.clear()
        self._update_resume_hint()
        self._status_var.set("Saved scan state cleared.")

    def _open_image_dialog(self) -> None:
        """Open the disk imaging dialog."""
        DiskImageDialog(self, self._targets, on_complete=self._refresh_devices)

    def _apply_results_filter(self) -> None:
        """Filter visible results by name, path, or type."""
        self._results_tree.apply_filter(self._filter_var.get().strip())

    def _on_device_selected(self, _event: tk.Event) -> None:
        """Store the currently highlighted storage target."""
        selection = self._device_list.curselection()
        if not selection:
            self._selected_target = None
            return
        index = int(selection[0])
        if 0 <= index < len(self._targets):
            self._selected_target = self._targets[index]

    def _start_scan(self) -> None:
        """Validate selection and launch the background scan worker."""
        if self._scan_worker.is_running:
            messagebox.showinfo(APP_NAME, "A scan is already running.")
            return

        if not self._selected_target:
            messagebox.showwarning(APP_NAME, "Please select a storage device or region first.")
            return

        target = self._selected_target
        if target.requires_root and not can_read_device(target.device_path):
            answer = messagebox.askyesno(
                APP_NAME,
                (
                    f"Reading {target.device_path} may require root privileges.\n\n"
                    "Continue anyway? (Restart the application with sudo for raw access.)"
                ),
            )
            if not answer:
                return

        self._results_tree.clear()
        self._details.show_entry(None)
        self._set_scan_controls(scanning=True)
        self._recover_button.configure(state="disabled")
        self._progress_var.set(0.0)

        resume = self._scan_worker.has_resumable_state
        if resume:
            self._restore_results_from_saved_state()

        started, error = self._scan_worker.start(
            target,
            resume=resume,
            scan_strategy=self._scan_mode_var.get(),
        )
        if not started:
            messagebox.showwarning(APP_NAME, error or "Could not start scan.")
            self._set_scan_controls(scanning=False)
            return

        mode_label = self._scan_mode_var.get().replace("_", " ")
        self._status_var.set(f"Scanning {target.name} ({mode_label})…")

    def _restore_results_from_saved_state(self) -> None:
        """Repopulate the results tree from a saved pause checkpoint."""
        state = self._state_manager.load()
        if not state:
            return
        entries = state.get("entries", [])
        if entries:
            self._results_tree.set_entries(entries)
            self._found_var.set(f"Found: {len(entries)}")

    def _toggle_pause(self) -> None:
        """Pause or resume the active scan."""
        if not self._scan_worker.is_running and self._scan_worker.has_resumable_state:
            self._scan_worker.resume_scan()
            self._pause_button.configure(text="Pause")
            self._set_scan_controls(scanning=True)
            self._status_var.set("Resuming scan…")
            return

        if self._scan_worker.is_paused:
            self._scan_worker.resume_scan()
            self._pause_button.configure(text="Pause")
            self._status_var.set("Scan resumed")
        else:
            self._scan_worker.pause()
            self._pause_button.configure(text="Resume")
            self._status_var.set("Scan paused – state saved")

    def _cancel_scan(self) -> None:
        """Cancel scanning and reset UI for a fresh start."""
        if self._scan_worker.is_running:
            self._scan_worker.cancel()
            self._status_var.set("Cancelling scan…")
        else:
            self._reset_after_scan(cancelled=True)

    def _on_scan_progress(self, progress: ScanProgress) -> None:
        """Thread-safe UI update for progress bar and labels."""
        self.after(0, lambda: self._apply_progress(progress))

    def _apply_progress(self, progress: ScanProgress) -> None:
        """Update progress widgets on the main thread."""
        self._progress_var.set(progress.percent)
        self._phase_var.set(f"Phase: {progress.phase}")
        self._eta_var.set(f"ETA: {progress.eta_display}")
        self._found_var.set(f"Found: {progress.entries_found}")

        if progress.pending_items:
            self._pending_var.set(f"Pending: {progress.pending_items}")
        else:
            self._pending_var.set("Pending: —")

        if progress.current_path:
            display_path = progress.current_path
            if len(display_path) > 80:
                display_path = "…" + display_path[-77:]
            self._status_var.set(display_path)

        if progress.is_paused:
            self._pause_button.configure(text="Resume")
        elif self._scan_worker.is_running:
            self._pause_button.configure(text="Pause")

    def _on_scan_entry(self, entry: RecoveryEntry) -> None:
        """Insert a newly found entry into the tree."""
        self.after(0, lambda: self._results_tree.add_entry(entry))

    def _on_scan_finished(
        self,
        entries: List[RecoveryEntry],
        finish_state: str,
        error_message: Optional[str],
    ) -> None:
        """Handle scan completion, pause, or cancellation on the UI thread."""
        self.after(
            0,
            lambda: self._handle_scan_finished(entries, finish_state, error_message),
        )

    def _handle_scan_finished(
        self,
        entries: List[RecoveryEntry],
        finish_state: str,
        error_message: Optional[str],
    ) -> None:
        """Finalize UI after the worker thread stops."""
        if finish_state == "error" or error_message:
            messagebox.showerror(APP_NAME, f"Scan failed:\n{error_message}")
            self._reset_after_scan(cancelled=True)
            return

        if finish_state == "cancelled":
            self._reset_after_scan(cancelled=True)
            return

        if finish_state == "paused":
            self._set_scan_controls(scanning=False, paused=True)
            self._recover_button.configure(state="normal")
            self._update_resume_hint()
            self._status_var.set(
                f"Scan paused. {len(entries)} items found. Select a destination to recover."
            )
            return

        self._results_tree.set_entries(entries)
        self._apply_results_filter()
        self._set_scan_controls(scanning=False)
        self._recover_button.configure(state="normal")
        self._progress_var.set(100.0)
        self._status_var.set(
            f"Scan complete. {len(entries)} items found. Select files and a destination."
        )

    def _reset_after_scan(self, cancelled: bool = False) -> None:
        """Restore default button states after cancel."""
        self._scan_worker.wait(timeout=2.0)
        self._set_scan_controls(scanning=False)
        if cancelled:
            self._results_tree.clear()
            self._details.show_entry(None)
            self._progress_var.set(0.0)
            self._status_var.set("Scan cancelled. Select a device and start again.")
        self._recover_button.configure(state="disabled")

    def _set_scan_controls(self, scanning: bool, paused: bool = False) -> None:
        """Enable/disable buttons depending on scan state."""
        if scanning:
            self._start_button.configure(state="disabled")
            self._pause_button.configure(state="normal", text="Pause")
            self._cancel_button.configure(state="normal")
            self._device_list.configure(state="disabled")
        elif paused:
            self._start_button.configure(state="normal")
            self._pause_button.configure(state="normal", text="Resume")
            self._cancel_button.configure(state="normal")
            self._device_list.configure(state="normal")
        else:
            self._start_button.configure(state="normal")
            self._pause_button.configure(state="disabled", text="Pause")
            self._cancel_button.configure(state="disabled")
            self._device_list.configure(state="normal")

    def _on_entry_selected(self, entry: Optional[RecoveryEntry]) -> None:
        """Show metadata for the highlighted tree row."""
        self._details.show_entry(entry)

    def _view_file(self, entry: RecoveryEntry) -> None:
        """Open the selected file with the system default application."""
        path = entry.preview_path or entry.extra.get("absolute_path")
        if not path or not os.path.exists(path):
            messagebox.showwarning(APP_NAME, "File is not available for preview.")
            return

        try:
            subprocess.Popen(["xdg-open", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open file:\n{exc}")

    def _browse_destination(self) -> None:
        """Pick the folder where recovered data will be written."""
        folder = filedialog.askdirectory(title="Select recovery destination")
        if folder:
            self._dest_var.set(folder)

    def _start_recovery(self) -> None:
        """Export all checked entries to the destination folder or ZIP."""
        if self._recovery_running:
            return

        destination = self._dest_var.get().strip()
        if not destination:
            messagebox.showwarning(APP_NAME, "Please choose a recovery destination folder.")
            return

        entries = self._results_tree.get_all_entries()
        selected_count = sum(1 for entry in entries if entry.selected)
        if selected_count == 0:
            messagebox.showwarning(APP_NAME, "Please check at least one item to recover.")
            return

        self._recovery_running = True
        self._recover_button.configure(state="disabled")
        self._status_var.set("Recovering selected data…")

        thread = threading.Thread(
            target=self._run_recovery,
            args=(entries, destination, self._zip_var.get()),
            daemon=True,
        )
        thread.start()

    def _run_recovery(self, entries: List[RecoveryEntry], destination: str, use_zip: bool) -> None:
        """Background export thread."""
        try:
            output_path = self._exporter.export(
                entries=entries,
                destination_dir=destination,
                use_zip=use_zip,
                on_progress=self._on_recovery_progress,
            )
            self.after(0, lambda: self._recovery_done(True, output_path, None))
        except Exception as exc:
            logger.exception("Recovery failed")
            self.after(0, lambda: self._recovery_done(False, None, str(exc)))

    def _on_recovery_progress(self, current: int, total: int, name: str) -> None:
        """Update status during export."""
        self.after(0, lambda: self._status_var.set(f"Recovering ({current}/{total}): {name}"))

    def _recovery_done(self, success: bool, output_path: Optional[str], error: Optional[str]) -> None:
        """Show result dialog and re-enable recovery button."""
        self._recovery_running = False
        self._recover_button.configure(state="normal")

        if success:
            messagebox.showinfo(APP_NAME, f"Recovery completed successfully.\n\nOutput:\n{output_path}")
            self._status_var.set("Recovery completed.")
        else:
            messagebox.showerror(APP_NAME, f"Recovery failed:\n{error}")
            self._status_var.set("Recovery failed.")

    def _show_about(self) -> None:
        """Display application information."""
        messagebox.showinfo(
            APP_NAME,
            (
                f"{APP_NAME} – Linux Data Recovery\n\n"
                "Scan disks, partitions, and unallocated space for recoverable files.\n"
                "Select items and export them to a folder or ZIP archive.\n\n"
                "Scan modes:\n"
                "  • Auto – filesystem inventory on mounted partitions, carving elsewhere\n"
                "  • Filesystem – inventory existing files on mounted partitions\n"
                "  • Deep carve – all signatures with format validation\n"
                "  • Quick carve – high-confidence signatures only (JPEG, PNG, PDF)\n\n"
                "Raw device access may require root privileges (sudo)."
            ),
        )

    def _on_close(self) -> None:
        """Clean shutdown: cancel active scan if needed."""
        if self._scan_worker.is_running:
            self._scan_worker.cancel()
            self._scan_worker.wait(timeout=3.0)
        self.destroy()

    def run(self) -> None:
        """Start the Tkinter main loop."""
        self.mainloop()
