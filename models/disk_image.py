"""
Disk image metadata model.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass
class DiskImageRecord:
    """
    Metadata for a sector-accurate disk image created by ByteBack.

    Attributes:
        image_id: Stable identifier used in the UI and registry.
        file_path: Absolute path to the ``.dd`` image file.
        source_device: Original block device path.
        size_bytes: Image size in bytes.
        sha256: SHA-256 hash of the image content.
        created_at: ISO timestamp when imaging completed.
        label: Optional user-visible label.
        bad_sector_ranges: ``(offset, length)`` byte ranges that could not be read
            from the source and were zero-filled in the image.
    """

    image_id: str
    file_path: str
    source_device: str
    size_bytes: int
    sha256: str
    created_at: str
    label: str = ""
    bad_sector_ranges: List[Tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize the record for JSON persistence."""
        return {
            "image_id": self.image_id,
            "file_path": self.file_path,
            "source_device": self.source_device,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": self.created_at,
            "label": self.label,
            "bad_sector_ranges": [[offset, length] for offset, length in self.bad_sector_ranges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DiskImageRecord":
        """Restore a record from persisted JSON."""
        return cls(
            image_id=data["image_id"],
            file_path=data["file_path"],
            source_device=data["source_device"],
            size_bytes=int(data.get("size_bytes", 0)),
            sha256=data.get("sha256", ""),
            created_at=data.get("created_at", ""),
            label=data.get("label", ""),
            bad_sector_ranges=[tuple(entry) for entry in data.get("bad_sector_ranges", [])],
        )

    @property
    def has_bad_sectors(self) -> bool:
        """True when one or more source ranges could not be read during imaging."""
        return len(self.bad_sector_ranges) > 0

    @property
    def display_name(self) -> str:
        """Human-readable label for device lists."""
        size_mb = self.size_bytes / (1024 * 1024)
        name = self.label or self.image_id
        suffix = f", {len(self.bad_sector_ranges)} bad sector range(s)" if self.has_bad_sectors else ""
        return f"{name}  [Disk Image, {size_mb:.1f} MiB{suffix}]"
