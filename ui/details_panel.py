"""
Side panel showing metadata for the currently selected recovery entry.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from models.recovery_entry import RecoveryEntry
from utils.file_info import human_size
from utils.hex_preview import format_hex_preview


class DetailsPanel(ttk.LabelFrame):
    """
    Displays file details and provides a button to open files externally.
    """

    def __init__(
        self,
        master: tk.Misc,
        on_view_file: Optional[Callable[[RecoveryEntry], None]] = None,
    ) -> None:
        """
        Args:
            master: Parent widget.
            on_view_file: Callback for the "View file" action.
        """
        super().__init__(master, text="File Details", padding=8)
        self._on_view_file = on_view_file
        self._current_entry: Optional[RecoveryEntry] = None

        self._name_var = tk.StringVar(value="—")
        self._path_var = tk.StringVar(value="—")
        self._type_var = tk.StringVar(value="—")
        self._size_var = tk.StringVar(value="—")
        self._mime_var = tk.StringVar(value="—")
        self._modified_var = tk.StringVar(value="—")
        self._confidence_var = tk.StringVar(value="—")
        self._extra_var = tk.StringVar(value="")

        self._build_widgets()

    def _build_widgets(self) -> None:
        """Lay out read-only detail fields."""
        fields = [
            ("Name", self._name_var),
            ("Path", self._path_var),
            ("Type", self._type_var),
            ("Size", self._size_var),
            ("MIME", self._mime_var),
            ("Modified", self._modified_var),
            ("Confidence", self._confidence_var),
        ]

        for row, (label, variable) in enumerate(fields):
            ttk.Label(self, text=f"{label}:").grid(row=row, column=0, sticky="nw", pady=2)
            ttk.Label(self, textvariable=variable, wraplength=260, justify="left").grid(
                row=row,
                column=1,
                sticky="w",
                pady=2,
            )

        ttk.Label(self, text="Info:").grid(row=len(fields), column=0, sticky="nw", pady=2)
        self._extra_label = ttk.Label(self, textvariable=self._extra_var, wraplength=260, justify="left")
        self._extra_label.grid(row=len(fields), column=1, sticky="w", pady=2)

        ttk.Label(self, text="Header (hex):").grid(row=len(fields) + 1, column=0, sticky="nw", pady=(8, 2))
        self._hex_text = tk.Text(self, height=8, width=36, wrap="none", relief="flat", borderwidth=1)
        self._hex_text.grid(row=len(fields) + 1, column=1, sticky="nsew", pady=(8, 2))
        self._hex_text.configure(state="disabled", font="TkFixedFont 9")

        self._view_button = ttk.Button(
            self,
            text="View file",
            command=self._handle_view_file,
            state="disabled",
        )
        self._view_button.grid(row=len(fields) + 2, column=0, columnspan=2, pady=(12, 0), sticky="ew")

    def show_entry(self, entry: Optional[RecoveryEntry]) -> None:
        """
        Populate the panel with data from the given entry.

        Args:
            entry: Selected recovery entry, or None to clear the panel.
        """
        self._current_entry = entry
        if not entry:
            self._clear()
            return

        self._name_var.set(entry.name)
        self._path_var.set(entry.relative_path)
        self._type_var.set(entry.entry_type.value.title())
        self._size_var.set("—" if entry.is_directory else human_size(entry.size_bytes))
        self._mime_var.set(entry.mime_type or "—")
        self._modified_var.set(entry.modified_time or "—")
        self._confidence_var.set(str(entry.extra.get("confidence", "—")).title())

        extra_parts = []
        if entry.is_carved:
            extra_parts.append(f"Carved from offset {entry.byte_offset}")
        if entry.extra.get("signature"):
            extra_parts.append(f"Signature: {entry.extra['signature']}")
        if entry.device_path:
            extra_parts.append(f"Device: {entry.device_path}")
        self._extra_var.set("\n".join(extra_parts) if extra_parts else "—")

        preview_source = entry.preview_path or entry.extra.get("absolute_path")
        self._set_hex_preview(format_hex_preview(preview_source))

        can_view = not entry.is_directory and (
            entry.preview_path or entry.extra.get("absolute_path")
        )
        self._view_button.configure(state="normal" if can_view else "disabled")

    def _clear(self) -> None:
        """Reset all fields to placeholders."""
        self._name_var.set("—")
        self._path_var.set("—")
        self._type_var.set("—")
        self._size_var.set("—")
        self._mime_var.set("—")
        self._modified_var.set("—")
        self._confidence_var.set("—")
        self._extra_var.set("")
        self._set_hex_preview("—")
        self._view_button.configure(state="disabled")

    def _set_hex_preview(self, text: str) -> None:
        """Update the read-only hex dump widget."""
        self._hex_text.configure(state="normal")
        self._hex_text.delete("1.0", tk.END)
        self._hex_text.insert("1.0", text)
        self._hex_text.configure(state="disabled")

    def _handle_view_file(self) -> None:
        """Invoke the view-file callback for the current entry."""
        if self._current_entry and self._on_view_file:
            self._on_view_file(self._current_entry)
