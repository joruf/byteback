"""
Represents a single file or directory discovered during a scan.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class EntryType(Enum):
    """Discovered entry classification."""

    FILE = "file"
    DIRECTORY = "directory"
    CARVED = "carved"  # file recovered from raw/unallocated space
    DELETED = "deleted"  # file recovered from ext4 deleted inode metadata


@dataclass
class RecoveryEntry:
    """
    One recoverable item shown in the results tree.

    Attributes:
        entry_id: Unique identifier within the current scan session.
        name: File or directory name.
        relative_path: Path relative to scan root (tree structure key).
        entry_type: File, directory, or carved artifact.
        size_bytes: Size in bytes (0 for directories).
        source_target_id: ID of the scanned StorageTarget.
        device_path: Block device used when carving (optional).
        byte_offset: Start offset on device for carved files.
        mime_type: Detected MIME type when available.
        extension: Suggested file extension.
        modified_time: Last modification timestamp (ISO string) when known.
        selected: Whether the user marked this entry for recovery.
        parent_id: Parent entry_id for tree hierarchy (None for roots).
        children_ids: Direct child entry IDs.
        preview_path: Temporary path for previewing carved content.
        extra: Additional metadata for the details panel.
    """

    entry_id: str
    name: str
    relative_path: str
    entry_type: EntryType
    size_bytes: int
    source_target_id: str
    device_path: Optional[str] = None
    byte_offset: int = 0
    mime_type: Optional[str] = None
    extension: Optional[str] = None
    modified_time: Optional[str] = None
    selected: bool = False
    parent_id: Optional[str] = None
    children_ids: list = field(default_factory=list)
    preview_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_directory(self) -> bool:
        """True when this entry represents a directory."""
        return self.entry_type == EntryType.DIRECTORY

    @property
    def is_carved(self) -> bool:
        """True when this entry was carved from raw space."""
        return self.entry_type == EntryType.CARVED

    @property
    def is_deleted(self) -> bool:
        """True when this entry was recovered from filesystem metadata."""
        return self.entry_type == EntryType.DELETED

    @property
    def recoverable_path(self) -> Optional[str]:
        """
        Filesystem path used for recovery copy, or preview path for carved files.
        """
        if self.preview_path:
            return self.preview_path
        if self.entry_type in (EntryType.FILE, EntryType.DIRECTORY):
            return self.extra.get("absolute_path")
        if self.entry_type in (EntryType.CARVED, EntryType.DELETED):
            return self.preview_path
        return None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize entry for scan-state persistence."""
        return {
            "entry_id": self.entry_id,
            "name": self.name,
            "relative_path": self.relative_path,
            "entry_type": self.entry_type.value,
            "size_bytes": self.size_bytes,
            "source_target_id": self.source_target_id,
            "device_path": self.device_path,
            "byte_offset": self.byte_offset,
            "mime_type": self.mime_type,
            "extension": self.extension,
            "modified_time": self.modified_time,
            "selected": self.selected,
            "parent_id": self.parent_id,
            "children_ids": list(self.children_ids),
            "preview_path": self.preview_path,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryEntry":
        """Restore entry from persisted scan state."""
        return cls(
            entry_id=data["entry_id"],
            name=data["name"],
            relative_path=data["relative_path"],
            entry_type=EntryType(data["entry_type"]),
            size_bytes=int(data.get("size_bytes", 0)),
            source_target_id=data["source_target_id"],
            device_path=data.get("device_path"),
            byte_offset=int(data.get("byte_offset", 0)),
            mime_type=data.get("mime_type"),
            extension=data.get("extension"),
            modified_time=data.get("modified_time"),
            selected=bool(data.get("selected", False)),
            parent_id=data.get("parent_id"),
            children_ids=list(data.get("children_ids", [])),
            preview_path=data.get("preview_path"),
            extra=dict(data.get("extra", {})),
        )
