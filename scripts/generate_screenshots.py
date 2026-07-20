#!/usr/bin/env python3
"""
Generate README screenshots for ByteBack.
"""

import subprocess
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable, Dict, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.scan_settings import SCAN_MODE_DEEP_CARVE
from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget, TargetType
from ui.image_dialog import DiskImageDialog
from ui.main_window import MainWindow
from ui.window_icon import apply_window_icon

SCREENSHOT_DIR = PROJECT_ROOT / "docs" / "screenshots"


def _patch_dialog_blocking() -> None:
    """Disable modal grabs that require a running mainloop during capture."""

    def wait_visibility(self) -> None:
        self.update_idletasks()
        self.update()

    def grab_set(self) -> None:
        return

    tk.Misc.wait_visibility = wait_visibility  # type: ignore[method-assign]
    tk.Misc.grab_set = grab_set  # type: ignore[method-assign]


_patch_dialog_blocking()


def demo_targets() -> list[StorageTarget]:
    """Return representative storage targets for screenshots."""
    gib = 1024 ** 3
    return [
        StorageTarget(
            target_id="disk_nvme0",
            name="nvme0n1",
            device_path="/dev/nvme0n1",
            target_type=TargetType.DISK,
            size_bytes=512 * gib,
            description="Whole disk /dev/nvme0n1",
        ),
        StorageTarget(
            target_id="part_home",
            name="nvme0n1p2",
            device_path="/dev/nvme0n1p2",
            target_type=TargetType.PARTITION,
            size_bytes=186 * gib,
            mountpoint="/home",
            parent_disk="nvme0n1",
            filesystem="ext4",
            description="Home partition",
        ),
        StorageTarget(
            target_id="part_boot",
            name="nvme0n1p1",
            device_path="/dev/nvme0n1p1",
            target_type=TargetType.PARTITION,
            size_bytes=1 * gib,
            mountpoint="/boot/efi",
            parent_disk="nvme0n1",
            filesystem="vfat",
            description="EFI system partition",
        ),
        StorageTarget(
            target_id="unalloc_sda",
            name="unallocated-sda",
            device_path="/dev/sda",
            target_type=TargetType.UNALLOCATED,
            size_bytes=8 * gib,
            parent_disk="sda",
            start_offset=500 * gib,
            description="Unallocated gap on /dev/sda",
        ),
        StorageTarget(
            target_id="image_usb",
            name="usb-backup.dd",
            device_path=str(Path.home() / ".local/share/byteback/images/usb-backup.dd"),
            target_type=TargetType.IMAGE,
            size_bytes=64 * gib,
            description="Saved disk image",
        ),
    ]


def demo_entries() -> list[RecoveryEntry]:
    """Return representative recovery results for screenshots."""
    return [
        RecoveryEntry(
            entry_id="carved_001",
            name="carved_1048576.jpg",
            relative_path="/carved/carved_1048576.jpg",
            entry_type=EntryType.CARVED,
            size_bytes=2_458_112,
            source_target_id="unalloc_sda",
            device_path="/dev/sda",
            byte_offset=1048576,
            mime_type="image/jpeg",
            extension=".jpg",
            selected=True,
            preview_path="/tmp/byteback_previews/carved_1048576.jpg",
            extra={"signature": "JPEG", "confidence": "high", "absolute_offset": 1048576},
        ),
        RecoveryEntry(
            entry_id="carved_002",
            name="carved_5242880.pdf",
            relative_path="/carved/carved_5242880.pdf",
            entry_type=EntryType.CARVED,
            size_bytes=845_221,
            source_target_id="unalloc_sda",
            device_path="/dev/sda",
            byte_offset=5242880,
            mime_type="application/pdf",
            extension=".pdf",
            selected=True,
            preview_path="/tmp/byteback_previews/carved_5242880.pdf",
            extra={"signature": "PDF", "confidence": "high", "absolute_offset": 5242880},
        ),
        RecoveryEntry(
            entry_id="deleted_001",
            name="report_2024.docx",
            relative_path="/home/user/Documents/report_2024.docx",
            entry_type=EntryType.DELETED,
            size_bytes=128_450,
            source_target_id="part_home",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            extension=".docx",
            modified_time="2024-11-18 14:22:07",
            selected=False,
            extra={"confidence": "high", "inode": 184502},
        ),
        RecoveryEntry(
            entry_id="file_001",
            name="notes.txt",
            relative_path="/home/user/notes.txt",
            entry_type=EntryType.FILE,
            size_bytes=4096,
            source_target_id="part_home",
            mime_type="text/plain",
            extension=".txt",
            modified_time="2025-03-02 09:15:44",
            selected=False,
            extra={"absolute_path": "/home/user/notes.txt"},
        ),
    ]


class ScreenshotMainWindow(MainWindow):
    """Main window configured for static screenshot capture."""

    def _start_control_server(self) -> None:
        return

    def _refresh_devices(self) -> None:
        self._targets = demo_targets()
        self._target_by_id = {target.target_id: target for target in self._targets}
        self._device_list.delete(0, tk.END)
        for target in self._targets:
            self._device_list.insert(tk.END, target.display_name)
        self._selected_target = None
        self._update_start_button_state()
        self._status_var.set(f"Found {len(self._targets)} storage targets.")


def capture_window(widget: tk.Misc, output_path: Path) -> None:
    """
    Capture a Tk window to a PNG file.

    Args:
        widget: Tk widget whose top-level window should be captured.
        output_path: Destination PNG path.
    """
    top = widget.winfo_toplevel()
    top.update_idletasks()
    top.update()
    top.deiconify()
    top.lift()
    top.attributes("-topmost", True)
    top.update_idletasks()
    top.update()
    time.sleep(0.35)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    window_id = top.winfo_id()

    commands = [
        ["import", "-window", str(window_id), str(output_path)],
        ["scrot", "-u", str(output_path)],
    ]
    last_error = None
    for command in commands:
        if not _command_available(command[0]):
            continue
        try:
            subprocess.run(
                command,
                check=True,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            top.attributes("-topmost", False)
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            last_error = exc

    top.attributes("-topmost", False)
    raise RuntimeError(f"Could not capture screenshot for {output_path}: {last_error}")


def _command_available(name: str) -> bool:
    """Return True when a capture utility exists on PATH."""
    from shutil import which

    return which(name) is not None


def apply_ready_state(app: ScreenshotMainWindow) -> None:
    """Populate the main window before the first screenshot."""
    app.geometry("1280x860")
    app._device_list.selection_set(1)
    app._on_device_selected(None)
    app._scan_mode_var.set(SCAN_MODE_DEEP_CARVE)
    app._status_var.set("Found 5 storage targets.")
    app.update_idletasks()
    app.update()


def apply_scan_results_state(app: ScreenshotMainWindow) -> None:
    """Populate the main window with demo scan results."""
    apply_ready_state(app)
    app._device_list.selection_set(3)
    app._on_device_selected(None)
    app._set_scan_controls(scanning=False)
    app._recover_button.configure(state="normal")
    app._dest_var.set(str(Path.home() / "Recovery" / "byteback-export"))
    app._progress_var.set(100.0)
    app._phase_var.set("Phase: deep_carve")
    app._eta_var.set("ETA: —")
    app._found_var.set("Found: 4")
    app._pending_var.set("Pending: —")
    app._status_var.set("Scan complete. 4 items found. Select files and a destination.")

    entries = demo_entries()
    app._results_tree.set_entries(entries)
    app._details.show_entry(entries[0])
    app.update_idletasks()
    app.update()


def _hidden_parent() -> tk.Tk:
    parent = tk.Tk()
    parent.withdraw()
    apply_window_icon(parent)
    return parent


def _capture_main_window_ready(output_path: Path) -> None:
    app = ScreenshotMainWindow()
    apply_ready_state(app)
    capture_window(app, output_path)
    app.destroy()


def _capture_main_window_results(output_path: Path) -> None:
    app = ScreenshotMainWindow()
    apply_scan_results_state(app)
    capture_window(app, output_path)
    app.destroy()


def _capture_disk_image_dialog(output_path: Path) -> None:
    parent = _hidden_parent()
    try:
        dialog = DiskImageDialog(parent, demo_targets())
        dialog._source_var.set("/dev/nvme0n1")
        dialog._dest_var.set(str(Path.home() / "Images" / "nvme0n1-backup.dd"))
        dialog._status_var.set("Select a source device and destination.")
        dialog.update_idletasks()
        dialog.update()
        capture_window(dialog, output_path)
        dialog.destroy()
    finally:
        parent.destroy()


def generate_screenshots(outputs: Iterable[Path] | None = None) -> list[Path]:
    """
    Generate README screenshots.

    Args:
        outputs: Optional explicit output paths to generate.

    Returns:
        List of generated screenshot paths.
    """
    handlers: Dict[str, Callable[[Path], None]] = {
        "main-window-ready.png": _capture_main_window_ready,
        "main-window-results.png": _capture_main_window_results,
        "disk-image-dialog.png": _capture_disk_image_dialog,
    }

    if outputs is not None:
        selected = {path.name: handlers[path.name] for path in outputs if path.name in handlers}
    else:
        selected = handlers

    generated: list[Path] = []
    for filename, handler in selected.items():
        output_path = SCREENSHOT_DIR / filename
        handler(output_path)
        generated.append(output_path)
        print(f"Wrote {output_path}")

    return generated


def main() -> int:
    """Generate all README screenshots."""
    if not os_display_available():
        print("DISPLAY is not available. Cannot generate Tkinter screenshots.", file=sys.stderr)
        return 1

    generate_screenshots()
    return 0


def os_display_available() -> bool:
    """Return True when a graphical display is available."""
    import os

    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


if __name__ == "__main__":
    raise SystemExit(main())
