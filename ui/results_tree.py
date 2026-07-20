"""
Results tree with checkbox selection and parent/child propagation.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from config import CHECK_OFF, CHECK_ON
from models.recovery_entry import RecoveryEntry


class ResultsTree(ttk.Frame):
    """
    Treeview showing discovered files/directories with checkbox column.

    Clicking a parent checkbox selects or deselects all descendants.
    """

    def __init__(
        self,
        master: tk.Misc,
        on_select_entry: Optional[Callable[[Optional[RecoveryEntry]], None]] = None,
        on_open_entry: Optional[Callable[[RecoveryEntry], None]] = None,
    ) -> None:
        """
        Args:
            master: Parent widget.
            on_select_entry: Callback when the user selects a tree row.
            on_open_entry: Callback when the user double-clicks an openable file.
        """
        super().__init__(master)
        self._on_select_entry = on_select_entry
        self._on_open_entry = on_open_entry
        self._entries_by_id: Dict[str, RecoveryEntry] = {}
        self._tree_id_to_entry: Dict[str, RecoveryEntry] = {}
        self._entry_to_tree_id: Dict[str, str] = {}
        self._updating = False
        self._filter_text = ""

        self._build_widgets()
        self._bind_events()

    def _build_widgets(self) -> None:
        """Create treeview and scrollbars."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        columns = ("checked", "type", "size")
        self.tree = ttk.Treeview(
            self,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Name / Path", anchor="w")
        self.tree.heading("checked", text="✓", anchor="center")
        self.tree.heading("type", text="Type", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")

        self.tree.column("#0", width=420, stretch=True)
        self.tree.column("checked", width=40, stretch=False, anchor="center")
        self.tree.column("type", width=100, stretch=False)
        self.tree.column("size", width=90, stretch=False, anchor="e")

        y_scroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        x_scroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")

    def _bind_events(self) -> None:
        """Wire selection and checkbox toggle handlers."""
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double_click)

    def clear(self) -> None:
        """Remove all items from the tree."""
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        self._tree_id_to_entry.clear()
        self._entry_to_tree_id.clear()

    def set_entries(self, entries: List[RecoveryEntry]) -> None:
        """
        Rebuild the full tree from a flat entry list.

        Args:
            entries: All RecoveryEntry objects for the current scan.
        """
        self._entries_by_id = {entry.entry_id: entry for entry in entries}
        self._rebuild_tree()

    def apply_filter(self, filter_text: str) -> None:
        """
        Show only entries matching the filter string.

        Args:
            filter_text: Case-insensitive substring matched against name,
                relative path, MIME type, and entry type.
        """
        self._filter_text = filter_text.strip().lower()
        self._rebuild_tree()

    def _entry_matches_filter(self, entry: RecoveryEntry) -> bool:
        """Return True when the entry passes the active filter."""
        if not self._filter_text:
            return True
        haystack = " ".join(
            [
                entry.name,
                entry.relative_path,
                entry.entry_type.value,
                entry.mime_type or "",
                str(entry.extra.get("signature", "")),
            ]
        ).lower()
        return self._filter_text in haystack

    def _rebuild_tree(self) -> None:
        """Rebuild tree rows from stored entries and the active filter."""
        self.clear()
        visible_ids = {
            entry.entry_id
            for entry in self._entries_by_id.values()
            if self._entry_matches_filter(entry)
        }
        if not visible_ids:
            return

        included_ids = set(visible_ids)
        for entry_id in visible_ids:
            current = self._entries_by_id.get(entry_id)
            while current and current.parent_id:
                included_ids.add(current.parent_id)
                current = self._entries_by_id.get(current.parent_id)

        filtered_entries = [
            entry for entry in self._entries_by_id.values() if entry.entry_id in included_ids
        ]
        roots = [entry for entry in filtered_entries if not entry.parent_id]
        roots.sort(key=lambda item: item.relative_path)

        for entry in roots:
            self._insert_entry_recursive(entry)

    def add_entry(self, entry: RecoveryEntry) -> None:
        """
        Insert a single entry incrementally during scanning.

        Args:
            entry: Newly discovered entry.
        """
        self._entries_by_id[entry.entry_id] = entry
        if not self._entry_matches_filter(entry):
            return
        if entry.parent_id and entry.parent_id in self._entry_to_tree_id:
            parent_tree_id = self._entry_to_tree_id[entry.parent_id]
            self._insert_tree_item(entry, parent_tree_id)
        elif not entry.parent_id:
            self._insert_tree_item(entry, "")

    def get_all_entries(self) -> List[RecoveryEntry]:
        """Return the current entry list including selection state."""
        return list(self._entries_by_id.values())

    def get_selected_entry(self) -> Optional[RecoveryEntry]:
        """Return the entry for the currently highlighted row."""
        selection = self.tree.selection()
        if not selection:
            return None
        return self._tree_id_to_entry.get(selection[0])

    def _insert_entry_recursive(self, entry: RecoveryEntry, parent_tree_id: str = "") -> None:
        """Insert entry and all descendants."""
        tree_id = self._insert_tree_item(entry, parent_tree_id)
        children = [
            self._entries_by_id[child_id]
            for child_id in entry.children_ids
            if child_id in self._entries_by_id
        ]
        children.sort(key=lambda item: item.name.lower())
        for child in children:
            self._insert_entry_recursive(child, tree_id)

    def _insert_tree_item(self, entry: RecoveryEntry, parent_tree_id: str) -> str:
        """Create one tree row and register mappings."""
        check_symbol = CHECK_ON if entry.selected else CHECK_OFF
        type_label = entry.entry_type.value.title()
        size_label = self._format_size(entry)

        tree_id = self.tree.insert(
            parent_tree_id,
            "end",
            text=entry.name,
            values=(check_symbol, type_label, size_label),
            open=True,
        )
        self._tree_id_to_entry[tree_id] = entry
        self._entry_to_tree_id[entry.entry_id] = tree_id
        return tree_id

    def _on_tree_select(self, _event: tk.Event) -> None:
        """Notify listener when row selection changes."""
        if self._on_select_entry:
            self._on_select_entry(self.get_selected_entry())

    def _on_click(self, event: tk.Event) -> None:
        """Toggle checkbox when the user clicks the check column."""
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        column = self.tree.identify_column(event.x)
        if column != "#1":
            return

        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        entry = self._tree_id_to_entry.get(row_id)
        if not entry:
            return

        new_state = not entry.selected
        self._set_entry_selected(entry, new_state)
        self._update_tree_checkbox(row_id, new_state)

    def _on_double_click(self, event: tk.Event) -> None:
        """Open a file with the system default application on double-click."""
        region = self.tree.identify_region(event.x, event.y)
        if region not in ("cell", "tree"):
            return

        column = self.tree.identify_column(event.x)
        if column == "#1":
            return

        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return

        entry = self._tree_id_to_entry.get(row_id)
        if not entry or entry.is_directory or not self._on_open_entry:
            return

        preview_source = entry.preview_path or entry.extra.get("absolute_path")
        if preview_source:
            self._on_open_entry(entry)

    def _set_entry_selected(self, entry: RecoveryEntry, selected: bool) -> None:
        """Apply selection to entry and all descendants."""
        self._updating = True
        try:
            entry.selected = selected
            self._propagate_to_children(entry, selected)
            if entry.parent_id:
                self._update_parent_state(entry.parent_id)
        finally:
            self._updating = False

    def _propagate_to_children(self, entry: RecoveryEntry, selected: bool) -> None:
        """Recursively select/deselect all children."""
        for child_id in entry.children_ids:
            child = self._entries_by_id.get(child_id)
            if not child:
                continue
            child.selected = selected
            tree_id = self._entry_to_tree_id.get(child_id)
            if tree_id:
                self._update_tree_checkbox(tree_id, selected)
            if child.children_ids:
                self._propagate_to_children(child, selected)

    def _update_parent_state(self, parent_id: str) -> None:
        """Set parent checked when all children are checked, otherwise unchecked."""
        parent = self._entries_by_id.get(parent_id)
        if not parent:
            return

        children = [
            self._entries_by_id[child_id]
            for child_id in parent.children_ids
            if child_id in self._entries_by_id
        ]
        if not children:
            return

        all_selected = all(child.selected for child in children)
        parent.selected = all_selected
        tree_id = self._entry_to_tree_id.get(parent_id)
        if tree_id:
            self._update_tree_checkbox(tree_id, all_selected)

        if parent.parent_id:
            self._update_parent_state(parent.parent_id)

    def _update_tree_checkbox(self, tree_id: str, selected: bool) -> None:
        """Refresh checkbox symbol in the tree row."""
        values = list(self.tree.item(tree_id, "values"))
        if len(values) < 3:
            values = ["", "", ""]
        values[0] = CHECK_ON if selected else CHECK_OFF
        self.tree.item(tree_id, values=values)

    @staticmethod
    def _format_size(entry: RecoveryEntry) -> str:
        """Format entry size for the size column."""
        if entry.is_directory:
            return "—"
        size = entry.size_bytes
        if size < 1024:
            return f"{size} B"
        if size < 1024 ** 2:
            return f"{size / 1024:.1f} KiB"
        if size < 1024 ** 3:
            return f"{size / 1024 ** 2:.1f} MiB"
        return f"{size / 1024 ** 3:.1f} GiB"
