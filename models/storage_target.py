"""
Represents a scannable storage entity: whole disk, partition, or unallocated region.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TargetType(Enum):
    """Kind of storage target exposed in the device list."""

    DISK = "disk"
    PARTITION = "partition"
    UNALLOCATED = "unallocated"
    LOOP = "loop"
    IMAGE = "image"


@dataclass
class StorageTarget:
    """
    A disk, partition, or unallocated gap that can be selected for recovery scanning.

    Attributes:
        target_id: Stable identifier used for UI and scan state persistence.
        name: Human-readable label (e.g. ``nvme0n1p2``).
        device_path: Block device path (e.g. ``/dev/nvme0n1p2``).
        target_type: Classification of this storage entity.
        size_bytes: Total size in bytes (0 if unknown).
        mountpoint: Mount path when the target is mounted, else None.
        parent_disk: Name of the parent disk for partitions/unallocated regions.
        start_offset: Byte offset on parent disk (unallocated regions only).
        filesystem: Detected filesystem type, if any.
        description: Extra detail shown in the device list.
    """

    target_id: str
    name: str
    device_path: str
    target_type: TargetType
    size_bytes: int
    mountpoint: Optional[str] = None
    parent_disk: Optional[str] = None
    start_offset: int = 0
    filesystem: Optional[str] = None
    description: str = ""

    @property
    def display_name(self) -> str:
        """Formatted label for list boxes."""
        size = self.format_size(self.size_bytes)
        type_label = self.target_type.value.replace("_", " ").title()
        mount = f" → {self.mountpoint}" if self.mountpoint else ""
        return f"{self.name}  [{type_label}, {size}]{mount}"

    @property
    def scan_path(self) -> str:
        """
        Path used for scanning.

        Mounted partitions are walked on the filesystem; everything else uses the
        block device path (raw carving).
        """
        if self.mountpoint and self.target_type == TargetType.PARTITION:
            return self.mountpoint
        return self.device_path

    @property
    def requires_root(self) -> bool:
        """True when raw block-device access is needed."""
        if self.target_type == TargetType.UNALLOCATED:
            return True
        if self.target_type == TargetType.IMAGE:
            return False
        if self.target_type in (TargetType.DISK, TargetType.PARTITION) and not self.mountpoint:
            return True
        return False

    @staticmethod
    def format_size(size_bytes: int) -> str:
        """Convert a byte count to a human-readable string."""
        if size_bytes <= 0:
            return "unknown"
        units = ["B", "KiB", "MiB", "GiB", "TiB"]
        value = float(size_bytes)
        for unit in units:
            if value < 1024.0 or unit == units[-1]:
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{size_bytes} B"
