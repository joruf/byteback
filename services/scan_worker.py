"""
Background scan worker with pause, resume, and cancel support.
"""

import logging
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from config.scan_settings import ETA_UPDATE_INTERVAL, SCAN_MODE_AUTO
from models.recovery_entry import RecoveryEntry
from models.scan_progress import ScanProgress
from models.storage_target import StorageTarget
from services.scanning.scan_executor import ScanExecutor
from services.scan_state import ScanStateManager

logger = logging.getLogger(__name__)


class ScanWorker:
    """
    Runs storage scanning on a dedicated thread and reports progress via callbacks.
    """

    def __init__(
        self,
        on_progress: Optional[Callable[[ScanProgress], None]] = None,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_finished: Optional[
            Callable[[List[RecoveryEntry], str, Optional[str]], None]
        ] = None,
    ) -> None:
        """
        Args:
            on_progress: Called whenever progress changes (from worker thread).
            on_entry: Called when a new RecoveryEntry is found.
            on_finished: Called when scanning ends (complete, paused, or cancelled).
        """
        self._on_progress = on_progress
        self._on_entry = on_entry
        self._on_finished = on_finished

        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._cancel_event = threading.Event()
        self._running = False

        self._target: Optional[StorageTarget] = None
        self._entries: List[RecoveryEntry] = []
        self._entries_lock = threading.Lock()
        self._state_manager = ScanStateManager()
        self._executor = ScanExecutor()

        self._start_time = 0.0
        self._bytes_processed = 0
        self._bytes_total = 0
        self._scan_mode = "idle"
        self._scan_strategy = SCAN_MODE_AUTO
        self._filesystem_queue: List[str] = []
        self._carve_offset = 0
        self._ext4_inode_cursor = 0
        self._free_space_range_index = 0
        self._current_path = ""
        self._error_message: Optional[str] = None
        self._last_progress_emit = 0.0
        self._last_eta_emit = 0.0
        self._cached_eta_seconds: Optional[float] = None

    @property
    def is_running(self) -> bool:
        """True while a scan thread is active."""
        return self._running

    @property
    def is_paused(self) -> bool:
        """True when pause has been requested and acknowledged."""
        return self._pause_event.is_set()

    @property
    def entries(self) -> List[RecoveryEntry]:
        """Thread-safe copy of discovered entries."""
        with self._entries_lock:
            return list(self._entries)

    @property
    def has_resumable_state(self) -> bool:
        """True when a saved checkpoint exists on disk."""
        return self._state_manager.has_saved_state()

    @property
    def saved_state_summary(self) -> Optional[Dict[str, object]]:
        """Return summary of persisted scan state."""
        state = self._state_manager.load()
        if not state:
            return None
        return {
            "target_id": state.get("target_id"),
            "scan_mode": state.get("scan_mode"),
            "entry_count": len(state.get("entries", [])),
            "bytes_processed": state.get("bytes_processed", 0),
            "bytes_total": state.get("bytes_total", 0),
        }

    def start(
        self,
        target: StorageTarget,
        resume: bool = False,
        scan_strategy: str = SCAN_MODE_AUTO,
    ) -> Tuple[bool, Optional[str]]:
        """
        Begin scanning the given target on a background thread.

        Returns:
            Tuple of (started, error_message).
        """
        if self._running:
            return False, "A scan is already running."

        if resume:
            mismatch = self._validate_resume_target(target)
            if mismatch:
                return False, mismatch

        self._target = target
        self._scan_strategy = scan_strategy
        self._cancel_event.clear()
        self._pause_event.clear()
        self._error_message = None
        self._start_time = time.time()
        self._last_progress_emit = 0.0
        self._last_eta_emit = 0.0
        self._cached_eta_seconds = None

        if resume:
            self._load_resume_state(target)
        else:
            self._entries = []
            self._bytes_processed = 0
            self._bytes_total = max(target.size_bytes, 1)
            self._filesystem_queue = []
            self._carve_offset = 0
            self._ext4_inode_cursor = 0
            self._free_space_range_index = 0
            self._state_manager.clear()

        self._running = True
        self._thread = threading.Thread(target=self._run_scan, name="byteback-scan", daemon=True)
        self._thread.start()
        return True, None

    def pause(self) -> None:
        """Request a pause at the next safe checkpoint."""
        self._pause_event.set()
        self._emit_progress(is_paused=True)

    def resume_scan(self) -> None:
        """Clear the pause flag so the worker continues."""
        if not self._running:
            if self._target:
                self.start(self._target, resume=True, scan_strategy=self._scan_strategy)
            return
        self._pause_event.clear()
        self._emit_progress(is_paused=False)

    def cancel(self) -> None:
        """Abort the current scan and allow a fresh start."""
        self._cancel_event.set()
        self._pause_event.clear()

    def wait(self, timeout: Optional[float] = None) -> None:
        """Block until the worker thread exits."""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    def _validate_resume_target(self, target: StorageTarget) -> Optional[str]:
        """Ensure saved state belongs to the selected storage target."""
        state = self._state_manager.load()
        if not state:
            return None
        saved_target = state.get("target_id")
        if saved_target and saved_target != target.target_id:
            return (
                f"Saved scan state belongs to a different target ({saved_target}). "
                f"Select the matching device or cancel the saved state first."
            )
        return None

    def _load_resume_state(self, target: StorageTarget) -> None:
        """Populate worker fields from persisted JSON state."""
        state = self._state_manager.load()
        if not state:
            self._entries = []
            self._bytes_processed = 0
            self._bytes_total = max(target.size_bytes, 1)
            self._filesystem_queue = []
            self._carve_offset = 0
            self._ext4_inode_cursor = 0
            self._free_space_range_index = 0
            return

        self._entries = state.get("entries", [])
        self._bytes_processed = int(state.get("bytes_processed", 0))
        self._bytes_total = int(state.get("bytes_total", target.size_bytes))
        self._filesystem_queue = list(state.get("filesystem_queue", []))
        self._carve_offset = int(state.get("carve_offset", 0))
        self._ext4_inode_cursor = int(state.get("ext4_inode_cursor", 0))
        self._free_space_range_index = int(state.get("free_space_range_index", 0))
        self._scan_mode = state.get("scan_mode", "filesystem")

    def _run_scan(self) -> None:
        """Main worker loop executed on the background thread."""
        assert self._target is not None
        target = self._target
        paused = False
        cancelled = False

        try:
            from services.scanning.scan_strategy import ScanStrategyResolver

            expected_mode = ScanStrategyResolver().resolve(target, self._scan_strategy)
            interval = ScanExecutor.progress_interval_for_mode(expected_mode)
            mode, entries, checkpoint = self._executor.execute(
                target=target,
                scan_strategy=self._scan_strategy,
                source_entries=self._entries,
                filesystem_queue=self._filesystem_queue,
                carve_offset=self._carve_offset,
                ext4_inode_cursor=self._ext4_inode_cursor,
                free_space_range_index=self._free_space_range_index,
                on_entry=self._handle_entry,
                on_progress=self._throttled_progress(interval),
                should_pause=self._pause_event.is_set,
                should_cancel=self._cancel_event.is_set,
            )

            with self._entries_lock:
                self._entries = entries

            self._scan_mode = mode
            self._bytes_processed = int(checkpoint.get("bytes_processed", 0))
            self._filesystem_queue = list(checkpoint.get("filesystem_queue", []))
            self._carve_offset = int(checkpoint.get("carve_offset", 0))
            self._ext4_inode_cursor = int(checkpoint.get("ext4_inode_cursor", 0))
            self._free_space_range_index = int(checkpoint.get("free_space_range_index", 0))

            cancelled = self._cancel_event.is_set()
            paused = self._pause_event.is_set() and not cancelled

            if paused:
                self._persist_state()
            elif not cancelled:
                self._state_manager.clear()
                if self._bytes_total > 0:
                    self._bytes_processed = self._bytes_total

        except Exception as exc:
            logger.exception("Scan failed")
            self._error_message = str(exc)
        finally:
            self._running = False
            complete = not paused and not cancelled and self._error_message is None
            self._emit_progress(
                is_paused=paused,
                is_complete=complete,
                is_cancelled=cancelled,
            )
            if cancelled:
                self._state_manager.clear()
                finish_state = "cancelled"
            elif paused:
                finish_state = "paused"
            elif complete:
                finish_state = "complete"
            else:
                finish_state = "error"

            if self._on_finished:
                self._on_finished(self.entries, finish_state, self._error_message)

    def _throttled_progress(self, interval: float) -> Callable[[int, int, str], None]:
        """Return a progress callback that limits UI update frequency."""
        def callback(processed: int, total: int, current_path: str) -> None:
            now = time.time()
            if now - self._last_progress_emit < interval:
                if not self._pause_event.is_set() and not self._cancel_event.is_set():
                    return
            self._last_progress_emit = now
            self._handle_byte_progress(processed, total, current_path)

        return callback

    def _handle_entry(self, entry: RecoveryEntry) -> None:
        """Append a discovered entry and notify the UI."""
        with self._entries_lock:
            self._entries.append(entry)
        if self._on_entry:
            self._on_entry(entry)
        self._emit_progress()

    def _handle_byte_progress(self, processed: int, total: int, current_path: str) -> None:
        """Update internal counters from scanner callbacks."""
        self._bytes_processed = processed
        if total > 0:
            self._bytes_total = total
        self._current_path = current_path
        self._emit_progress()

    def _persist_state(self) -> None:
        """Write checkpoint when pausing."""
        if not self._target:
            return
        self._state_manager.save(
            target_id=self._target.target_id,
            scan_mode=self._scan_mode,
            bytes_processed=self._bytes_processed,
            bytes_total=self._bytes_total,
            filesystem_queue=self._filesystem_queue,
            carve_offset=self._carve_offset,
            ext4_inode_cursor=self._ext4_inode_cursor,
            free_space_range_index=self._free_space_range_index,
            entries=self.entries,
        )

    def _emit_progress(
        self,
        is_paused: bool = False,
        is_complete: bool = False,
        is_cancelled: bool = False,
    ) -> None:
        """Build a ScanProgress snapshot and invoke the callback."""
        now = time.time()
        elapsed = now - self._start_time if self._start_time else 0.0
        eta = self._cached_eta_seconds

        if is_paused or is_complete or is_cancelled:
            eta = None
            self._cached_eta_seconds = None
            self._last_eta_emit = 0.0
        elif (
            self._last_eta_emit == 0.0
            or now - self._last_eta_emit >= ETA_UPDATE_INTERVAL
        ):
            eta = None
            if self._bytes_total > 0 and self._bytes_processed > 0:
                rate = self._bytes_processed / elapsed if elapsed > 0 else 0
                if rate > 0:
                    remaining = max(0, self._bytes_total - self._bytes_processed)
                    eta = remaining / rate
            self._cached_eta_seconds = eta
            self._last_eta_emit = now

        pending = ""
        if self._scan_mode == "filesystem" and self._filesystem_queue:
            pending = f"{len(self._filesystem_queue)} directories remaining"
        elif self._scan_mode in ("deep_carve", "quick_carve", "carve"):
            pending = f"Carving from offset {self._carve_offset}"
        elif self._scan_mode == "ext4_deleted":
            pending = f"Inode cursor {self._ext4_inode_cursor}"
        elif self._scan_mode == "free_space":
            pending = f"Free-space range {self._free_space_range_index}"

        progress = ScanProgress(
            bytes_processed=self._bytes_processed,
            bytes_total=self._bytes_total,
            current_path=self._current_path,
            entries_found=len(self._entries),
            phase=self._scan_mode,
            elapsed_seconds=elapsed,
            eta_seconds=eta,
            pending_items=pending,
            is_paused=is_paused or (self._pause_event.is_set() and self._running),
            is_complete=is_complete,
            is_cancelled=is_cancelled,
            error_message=self._error_message,
        )

        if self._on_progress:
            self._on_progress(progress)
