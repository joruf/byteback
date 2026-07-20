"""
Deep filesystem traversal for mounted partitions and accessible paths.
"""

import logging
import os
import uuid
from collections import deque
from typing import Callable, Dict, List, Optional, Set, Tuple

from models.recovery_entry import EntryType, RecoveryEntry
from models.storage_target import StorageTarget
from utils.file_info import detect_mime_type, format_timestamp

logger = logging.getLogger(__name__)


class FilesystemScanner:
    """
    Walks a directory tree and emits RecoveryEntry objects for files and folders.

    Supports pause/resume via an explicit directory queue persisted in scan state.
    """

    def scan(
        self,
        target: StorageTarget,
        source_target_id: str,
        initial_queue: Optional[List[str]] = None,
        known_entries: Optional[Dict[str, RecoveryEntry]] = None,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[RecoveryEntry], List[str], int]:
        """
        Perform a depth-first scan of the target mount path.

        Args:
            target: Selected storage target (must have mountpoint).
            source_target_id: ID stored on each RecoveryEntry.
            initial_queue: Remaining directories when resuming.
            known_entries: Existing entries keyed by entry_id (resume support).
            on_entry: Callback for each newly discovered entry.
            on_progress: Callback(bytes_done, bytes_total, current_path).
            should_pause: Returns True when scanning should pause.
            should_cancel: Returns True when scanning should abort.

        Returns:
            Tuple of (all entries, remaining directory queue, bytes processed estimate).
        """
        root_path = target.scan_path
        if not os.path.isdir(root_path):
            raise FileNotFoundError(f"Scan path is not accessible: {root_path}")

        entries_by_id: Dict[str, RecoveryEntry] = dict(known_entries or {})
        path_to_id: Dict[str, str] = {
            entry.relative_path: entry.entry_id for entry in entries_by_id.values()
        }
        dir_queue: deque = deque(initial_queue or [root_path])
        visited_dirs: Set[str] = set()
        bytes_processed = 0
        total_estimate = max(target.size_bytes, 1)

        while dir_queue:
            if should_cancel and should_cancel():
                break

            while should_pause and should_pause():
                if should_cancel and should_cancel():
                    return list(entries_by_id.values()), list(dir_queue), bytes_processed
                import time

                time.sleep(0.2)

            current_dir = dir_queue.popleft()
            if current_dir in visited_dirs:
                continue
            visited_dirs.add(current_dir)

            rel_dir = self._relative_path(root_path, current_dir)
            if rel_dir not in path_to_id:
                dir_entry = self._create_directory_entry(
                    current_dir,
                    rel_dir,
                    source_target_id,
                    root_path,
                )
                self._attach_to_parent(dir_entry, entries_by_id, path_to_id)
                entries_by_id[dir_entry.entry_id] = dir_entry
                path_to_id[rel_dir] = dir_entry.entry_id
                if on_entry:
                    on_entry(dir_entry)

            if on_progress:
                on_progress(bytes_processed, total_estimate, current_dir)

            try:
                with os.scandir(current_dir) as scanner:
                    for item in scanner:
                        if should_cancel and should_cancel():
                            return list(entries_by_id.values()), list(dir_queue), bytes_processed

                        item_path = item.path
                        rel_path = self._relative_path(root_path, item_path)

                        if item.is_dir(follow_symlinks=False):
                            dir_queue.append(item_path)
                            continue

                        if item.is_symlink():
                            continue

                        if not item.is_file(follow_symlinks=False):
                            continue

                        try:
                            stat = item.stat(follow_symlinks=False)
                            size = stat.st_size
                            mtime = format_timestamp(stat.st_mtime)
                        except OSError:
                            size = 0
                            mtime = None

                        file_entry = RecoveryEntry(
                            entry_id=f"file_{uuid.uuid4().hex[:12]}",
                            name=item.name,
                            relative_path=rel_path,
                            entry_type=EntryType.FILE,
                            size_bytes=size,
                            source_target_id=source_target_id,
                            mime_type=detect_mime_type(item_path),
                            extension=os.path.splitext(item.name)[1].lower() or None,
                            modified_time=mtime,
                            extra={"absolute_path": item_path},
                        )
                        self._attach_to_parent(file_entry, entries_by_id, path_to_id)
                        entries_by_id[file_entry.entry_id] = file_entry
                        path_to_id[rel_path] = file_entry.entry_id
                        bytes_processed += size

                        if on_entry:
                            on_entry(file_entry)

            except PermissionError:
                logger.debug("Permission denied: %s", current_dir)
            except OSError as exc:
                logger.debug("Cannot read directory %s: %s", current_dir, exc)

        return list(entries_by_id.values()), list(dir_queue), bytes_processed

    def _create_directory_entry(
        self,
        absolute_path: str,
        relative_path: str,
        source_target_id: str,
        root_path: str,
    ) -> RecoveryEntry:
        """Build a directory RecoveryEntry."""
        name = os.path.basename(absolute_path.rstrip(os.sep)) or absolute_path
        try:
            stat = os.stat(absolute_path)
            mtime = format_timestamp(stat.st_mtime)
        except OSError:
            mtime = None

        return RecoveryEntry(
            entry_id=f"dir_{uuid.uuid4().hex[:12]}",
            name=name,
            relative_path=relative_path,
            entry_type=EntryType.DIRECTORY,
            size_bytes=0,
            source_target_id=source_target_id,
            modified_time=mtime,
            extra={"absolute_path": absolute_path},
        )

    def _attach_to_parent(
        self,
        entry: RecoveryEntry,
        entries_by_id: Dict[str, RecoveryEntry],
        path_to_id: Dict[str, str],
    ) -> None:
        """Link an entry to its parent directory in the tree."""
        parent_rel = os.path.dirname(entry.relative_path)
        if parent_rel and parent_rel != "/" and parent_rel in path_to_id:
            parent_id = path_to_id[parent_rel]
            entry.parent_id = parent_id
            parent = entries_by_id[parent_id]
            if entry.entry_id not in parent.children_ids:
                parent.children_ids.append(entry.entry_id)

    @staticmethod
    def _relative_path(root_path: str, absolute_path: str) -> str:
        """Normalize a path relative to the scan root."""
        rel = os.path.relpath(absolute_path, root_path)
        if rel == ".":
            return "/"
        return "/" + rel.replace(os.sep, "/")
