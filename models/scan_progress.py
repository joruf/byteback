"""
Scan progress snapshot communicated from the worker thread to the UI.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class ScanProgress:
    """
    Current scan status for the progress bar and status labels.

    Attributes:
        bytes_processed: Number of bytes scanned so far.
        bytes_total: Total bytes to scan (0 if unknown).
        current_path: Human-readable description of current work item.
        entries_found: Count of discovered entries so far.
        phase: High-level phase name (e.g. ``filesystem``, ``carving``).
        elapsed_seconds: Time since scan start.
        eta_seconds: Estimated seconds remaining (None if not calculable).
        pending_items: Short description of remaining work queue.
        is_paused: Whether the scan is currently paused.
        is_complete: Whether scanning finished successfully.
        is_cancelled: Whether the user cancelled the scan.
        error_message: Error text when the scan failed.
    """

    bytes_processed: int = 0
    bytes_total: int = 0
    current_path: str = ""
    entries_found: int = 0
    phase: str = "idle"
    elapsed_seconds: float = 0.0
    eta_seconds: Optional[float] = None
    pending_items: str = ""
    is_paused: bool = False
    is_complete: bool = False
    is_cancelled: bool = False
    error_message: Optional[str] = None

    @property
    def percent(self) -> float:
        """Completion percentage between 0 and 100."""
        if self.bytes_total <= 0:
            return 0.0
        return min(100.0, (self.bytes_processed / self.bytes_total) * 100.0)

    @property
    def eta_display(self) -> str:
        """Formatted ETA string for the UI."""
        if self.eta_seconds is None or self.is_paused or self.is_complete:
            return "—"
        seconds = int(self.eta_seconds)
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
