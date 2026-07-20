"""
Persists and restores scan worker state to support pause/resume.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from config import STATE_DIR, STATE_FILENAME
from models.recovery_entry import RecoveryEntry

logger = logging.getLogger(__name__)


class ScanStateManager:
    """
    Saves scan progress to disk so a paused scan can resume later.

    State is stored as JSON under ``~/.local/share/byteback/``.
    """

    def __init__(self) -> None:
        """Ensure the state directory exists."""
        os.makedirs(STATE_DIR, exist_ok=True)
        self._state_path = os.path.join(STATE_DIR, STATE_FILENAME)

    @property
    def state_path(self) -> str:
        """Absolute path to the persisted state file."""
        return self._state_path

    def save(
        self,
        target_id: str,
        scan_mode: str,
        bytes_processed: int,
        bytes_total: int,
        filesystem_queue: List[str],
        carve_offset: int,
        entries: List[RecoveryEntry],
        ext4_inode_cursor: int = 0,
        free_space_range_index: int = 0,
    ) -> None:
        """
        Write the current scan checkpoint to disk.

        Args:
            target_id: StorageTarget.target_id being scanned.
            scan_mode: Internal scan mode name.
            bytes_processed: Progress counter (bytes or inodes depending on mode).
            bytes_total: Total work units for progress calculation.
            filesystem_queue: Remaining directories for filesystem scan.
            carve_offset: Current byte offset for raw carving.
            entries: All entries discovered up to this point.
            ext4_inode_cursor: Resume inode number for ext4 deleted scan.
            free_space_range_index: Resume index for free-space carving.
        """
        payload = {
            "target_id": target_id,
            "scan_mode": scan_mode,
            "bytes_processed": bytes_processed,
            "bytes_total": bytes_total,
            "filesystem_queue": filesystem_queue,
            "carve_offset": carve_offset,
            "ext4_inode_cursor": ext4_inode_cursor,
            "free_space_range_index": free_space_range_index,
            "entries": [entry.to_dict() for entry in entries],
        }
        try:
            with open(self._state_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            logger.error("Could not save scan state: %s", exc)

    def load(self) -> Optional[Dict[str, Any]]:
        """
        Load a previously saved scan state.

        Returns:
            Deserialized state dict, or None when no valid state exists.
        """
        if not os.path.isfile(self._state_path):
            return None
        try:
            with open(self._state_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            payload["entries"] = [
                RecoveryEntry.from_dict(item) for item in payload.get("entries", [])
            ]
            return payload
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.error("Could not load scan state: %s", exc)
            return None

    def clear(self) -> None:
        """Remove persisted scan state after cancel or successful completion."""
        try:
            if os.path.isfile(self._state_path):
                os.remove(self._state_path)
        except OSError as exc:
            logger.warning("Could not remove scan state: %s", exc)

    def has_saved_state(self) -> bool:
        """True when a resumable state file is present."""
        return os.path.isfile(self._state_path)
