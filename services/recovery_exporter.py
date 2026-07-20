"""
Exports selected recovery entries to a destination directory or ZIP archive.
"""

import logging
import os
import shutil
import zipfile
from typing import Callable, List, Optional

from models.recovery_entry import EntryType, RecoveryEntry
from services.file_carver import FileCarver
from utils.device_io import open_device

logger = logging.getLogger(__name__)


class RecoveryExporter:
    """
    Copies or archives user-selected entries to a chosen output location.

    Supports direct folder export and ZIP packaging.
    """

    def __init__(self) -> None:
        """Initialize helpers used for carved file extraction."""
        self._carver = FileCarver()

    def export(
        self,
        entries: List[RecoveryEntry],
        destination_dir: str,
        use_zip: bool = False,
        zip_name: str = "recovered_data.zip",
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> str:
        """
        Recover selected entries to disk.

        Args:
            entries: Selected RecoveryEntry objects (files and directories).
            destination_dir: Folder where data or the ZIP will be written.
            use_zip: When True, pack everything into a single ZIP archive.
            zip_name: Filename for the ZIP when ``use_zip`` is True.
            on_progress: Callback(current_index, total_count, item_name).

        Returns:
            Absolute path to the output directory or ZIP file.

        Raises:
            OSError: When the destination cannot be written.
            ValueError: When no recoverable entries were provided.
        """
        selected = [entry for entry in entries if entry.selected]
        if not selected:
            raise ValueError("No entries selected for recovery")

        os.makedirs(destination_dir, exist_ok=True)
        expanded = self._expand_selection(selected, entries)

        if use_zip:
            return self._export_zip(expanded, destination_dir, zip_name, on_progress)
        return self._export_direct(expanded, destination_dir, on_progress)

    def _expand_selection(
        self,
        selected: List[RecoveryEntry],
        all_entries: List[RecoveryEntry],
    ) -> List[RecoveryEntry]:
        """
        Include child files when a directory is selected.

        Args:
            selected: Directly selected entries.
            all_entries: Full entry list for child lookup.

        Returns:
            Deduplicated list of entries to export.
        """
        by_id = {entry.entry_id: entry for entry in all_entries}
        result = {}
        for entry in selected:
            result[entry.entry_id] = entry
            if entry.is_directory:
                self._collect_descendants(entry, by_id, result)
        return list(result.values())

    def _collect_descendants(
        self,
        parent: RecoveryEntry,
        by_id: dict,
        result: dict,
    ) -> None:
        """Recursively add all descendants of a directory entry."""
        for child_id in parent.children_ids:
            child = by_id.get(child_id)
            if not child:
                continue
            result[child.entry_id] = child
            if child.is_directory:
                self._collect_descendants(child, by_id, result)

    def _export_direct(
        self,
        entries: List[RecoveryEntry],
        destination_dir: str,
        on_progress: Optional[Callable[[int, int, str], None]],
    ) -> str:
        """Copy files and directory structures to the destination folder."""
        file_entries = [
            entry for entry in entries
            if entry.entry_type in (EntryType.FILE, EntryType.CARVED, EntryType.DELETED)
        ]
        total = len(file_entries)

        for index, entry in enumerate(file_entries, start=1):
            rel = entry.relative_path.lstrip("/")
            target_path = os.path.join(destination_dir, rel)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            source = self._resolve_source_path(entry)
            if not source:
                logger.warning("Skipping entry without source: %s", entry.name)
                continue

            if entry.is_carved or entry.is_deleted:
                shutil.copy2(source, target_path)
            else:
                shutil.copy2(source, target_path)

            if on_progress:
                on_progress(index, total, entry.name)

        return destination_dir

    def _export_zip(
        self,
        entries: List[RecoveryEntry],
        destination_dir: str,
        zip_name: str,
        on_progress: Optional[Callable[[int, int, str], None]],
    ) -> str:
        """Pack selected files into a ZIP archive."""
        zip_path = os.path.join(destination_dir, zip_name)
        file_entries = [
            entry for entry in entries
            if entry.entry_type in (EntryType.FILE, EntryType.CARVED, EntryType.DELETED)
        ]
        total = len(file_entries)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, entry in enumerate(file_entries, start=1):
                source = self._resolve_source_path(entry)
                if not source:
                    continue
                arcname = entry.relative_path.lstrip("/")
                archive.write(source, arcname=arcname)
                if on_progress:
                    on_progress(index, total, entry.name)

        return zip_path

    def _resolve_source_path(self, entry: RecoveryEntry) -> Optional[str]:
        """
        Determine the readable source path for an entry.

        Carved files may need to be re-read from the device when no preview exists.
        """
        if entry.preview_path and os.path.isfile(entry.preview_path):
            return entry.preview_path

        absolute = entry.extra.get("absolute_path")
        if absolute and os.path.exists(absolute):
            return absolute

        if entry.is_carved and entry.device_path and entry.byte_offset >= 0:
            preview = self._read_carved_to_temp(entry)
            return preview

        return None

    def _read_carved_to_temp(self, entry: RecoveryEntry) -> Optional[str]:
        """Re-extract a carved file from the block device for export."""
        if not entry.device_path:
            return None

        preview_dir = self._carver.preview_dir
        os.makedirs(preview_dir, exist_ok=True)
        extension = entry.extension or ".bin"
        target = os.path.join(preview_dir, f"export_{entry.entry_id}{extension}")

        try:
            max_read = min(entry.size_bytes or 10 * 1024 * 1024, 100 * 1024 * 1024)
            with open_device(entry.device_path) as device:
                device.seek(entry.byte_offset)
                data = device.read(max_read)
            with open(target, "wb") as handle:
                handle.write(data)
            return target
        except OSError as exc:
            logger.error("Could not re-read carved file %s: %s", entry.name, exc)
            return None
