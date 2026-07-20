"""
Persistent registry of disk images created by ByteBack.
"""

import json
import logging
import os
import uuid
from typing import List, Optional

from config.storage_paths import IMAGE_DIR, IMAGE_REGISTRY_FILENAME
from models.disk_image import DiskImageRecord
from models.storage_target import StorageTarget, TargetType

logger = logging.getLogger(__name__)


class DiskImageRegistry:
    """
    Stores and loads disk image metadata under ``~/.local/share/byteback/images/``.
    """

    def __init__(self) -> None:
        """Ensure image directories exist."""
        os.makedirs(IMAGE_DIR, exist_ok=True)
        self._registry_path = os.path.join(IMAGE_DIR, IMAGE_REGISTRY_FILENAME)

    def register(self, record: DiskImageRecord) -> DiskImageRecord:
        """
        Add or update an image record in the registry.

        Args:
            record: Completed image metadata.

        Returns:
            The stored record (may receive a generated image_id).
        """
        records = self.list_records()
        if not record.image_id:
            record.image_id = f"img_{uuid.uuid4().hex[:10]}"

        updated = [item for item in records if item.image_id != record.image_id]
        updated.append(record)
        self._save(updated)
        return record

    def list_records(self) -> List[DiskImageRecord]:
        """Return all registered disk images."""
        if not os.path.isfile(self._registry_path):
            return []
        try:
            with open(self._registry_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return [DiskImageRecord.from_dict(item) for item in payload.get("images", [])]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.error("Could not load image registry: %s", exc)
            return []

    def remove(self, image_id: str) -> None:
        """Remove an image record from the registry (does not delete the file)."""
        records = [item for item in self.list_records() if item.image_id != image_id]
        self._save(records)

    def as_storage_targets(self) -> List[StorageTarget]:
        """Convert registered images into scannable StorageTarget entries."""
        targets: List[StorageTarget] = []
        for record in self.list_records():
            if not os.path.isfile(record.file_path):
                continue
            targets.append(
                StorageTarget(
                    target_id=f"image:{record.image_id}",
                    name=record.label or record.image_id,
                    device_path=record.file_path,
                    target_type=TargetType.IMAGE,
                    size_bytes=record.size_bytes,
                    filesystem="raw_image",
                    description=(
                        f"Disk image of {record.source_device} "
                        f"(SHA-256 {record.sha256[:12]}…)"
                    ),
                )
            )
        return targets

    def _save(self, records: List[DiskImageRecord]) -> None:
        """Write registry JSON to disk."""
        payload = {"images": [record.to_dict() for record in records]}
        try:
            with open(self._registry_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except OSError as exc:
            logger.error("Could not save image registry: %s", exc)

    @staticmethod
    def default_image_path(source_device: str) -> str:
        """
        Build a default output path for a new disk image.

        Args:
            source_device: Source block device path.

        Returns:
            Absolute path ending in ``.dd``.
        """
        base = os.path.basename(source_device).replace("/", "_")
        return os.path.join(IMAGE_DIR, f"{base}_{uuid.uuid4().hex[:8]}.dd")
