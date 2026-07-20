"""
Enumerates block devices, partitions, and unallocated regions on Linux.
"""

import json
import logging
import subprocess
from typing import List, Optional

from models.storage_target import StorageTarget, TargetType

logger = logging.getLogger(__name__)


class DeviceScanner:
    """
    Discovers disks, partitions, and unallocated gaps using ``lsblk`` and ``parted``.

    The scanner is read-only and does not modify any storage device.
    """

    def scan_all(self) -> List[StorageTarget]:
        """
        Return all scannable storage targets sorted for display.

        Returns:
            List of StorageTarget instances including unallocated regions.
        """
        targets: List[StorageTarget] = []
        block_devices = self._read_lsblk()

        for device in block_devices:
            self._append_device_tree(device, targets)

        for target in list(targets):
            if target.target_type == TargetType.DISK:
                unallocated = self._find_unallocated_regions(target)
                targets.extend(unallocated)

        return self._sort_targets(targets)

    def _read_lsblk(self) -> list:
        """Parse ``lsblk -J`` JSON output."""
        try:
            result = subprocess.run(
                [
                    "lsblk",
                    "-J",
                    "-b",
                    "-o",
                    "NAME,SIZE,TYPE,MOUNTPOINT,FSTYPE,RM,RO",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            payload = json.loads(result.stdout)
            return payload.get("blockdevices", [])
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to read block devices: %s", exc)
            return []

    def _append_device_tree(self, node: dict, targets: List[StorageTarget], parent: Optional[str] = None) -> None:
        """Recursively walk lsblk nodes and build StorageTarget entries."""
        name = node.get("name", "")
        node_type = node.get("type", "")
        size = int(node.get("size") or 0)
        mountpoint = self._resolve_mountpoint(node)
        fstype = node.get("fstype") or None
        device_path = f"/dev/{name}"

        if node_type == "disk":
            target_type = TargetType.DISK
            target_id = f"disk:{name}"
            description = f"Whole disk {device_path}"
        elif node_type == "part":
            target_type = TargetType.PARTITION
            target_id = f"part:{name}"
            description = f"Partition {device_path}"
            if fstype:
                description += f" ({fstype})"
        elif node_type == "loop":
            target_type = TargetType.LOOP
            target_id = f"loop:{name}"
            description = f"Loop device {device_path}"
        else:
            for child in node.get("children") or []:
                self._append_device_tree(child, targets, parent=name)
            return

        targets.append(
            StorageTarget(
                target_id=target_id,
                name=name,
                device_path=device_path,
                target_type=target_type,
                size_bytes=size,
                mountpoint=mountpoint,
                parent_disk=parent,
                filesystem=fstype,
                description=description,
            )
        )

        for child in node.get("children") or []:
            self._append_device_tree(child, targets, parent=name if node_type == "disk" else parent)

    def _find_unallocated_regions(self, disk: StorageTarget) -> List[StorageTarget]:
        """
        Detect unallocated gaps on a disk via ``parted`` machine output.

        Falls back to an empty list when parted is unavailable.
        """
        regions: List[StorageTarget] = []
        parted_output = self._run_parted_free(disk.device_path)
        if not parted_output:
            return regions

        index = 0
        for line in parted_output.splitlines():
            if not line.startswith("1:"):
                continue
            # Machine format: num:start:end:size:filesystem:name:flags
            parts = line.split(":")
            if len(parts) < 5:
                continue
            region_type = parts[4]
            if region_type != "free":
                continue

            try:
                start = int(parts[1].rstrip("B"))
                end = int(parts[2].rstrip("B"))
                size = int(parts[3].rstrip("B"))
            except ValueError:
                continue

            if size <= 0:
                continue

            index += 1
            region_name = f"{disk.name}_unallocated_{index}"
            regions.append(
                StorageTarget(
                    target_id=f"unalloc:{disk.name}:{start}",
                    name=region_name,
                    device_path=disk.device_path,
                    target_type=TargetType.UNALLOCATED,
                    size_bytes=size,
                    parent_disk=disk.name,
                    start_offset=start,
                    description=(
                        f"Unallocated space on {disk.device_path} "
                        f"({StorageTarget.format_size(size)} @ offset {start})"
                    ),
                )
            )

        return regions

    def _run_parted_free(self, device_path: str) -> Optional[str]:
        """Execute ``parted -m unit B print free`` and return stdout."""
        try:
            result = subprocess.run(
                ["parted", "-s", "-m", device_path, "unit", "B", "print", "free"],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if result.returncode != 0:
                logger.debug("parted failed for %s: %s", device_path, result.stderr.strip())
                return None
            return result.stdout
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("parted unavailable for %s: %s", device_path, exc)
            return None

    @staticmethod
    def _resolve_mountpoint(node: dict) -> Optional[str]:
        """
        Return the mount path from lsblk output.

        Supports both legacy ``mountpoint`` (string) and newer ``mountpoints``
        (array) fields returned by different util-linux versions.
        """
        direct = node.get("mountpoint")
        if direct:
            return direct

        mountpoints = node.get("mountpoints") or []
        for mount in mountpoints:
            if mount:
                return mount
        return None

    @staticmethod
    def _first_mountpoint(mountpoints: list) -> Optional[str]:
        """Return the first non-null mountpoint from lsblk."""
        for mount in mountpoints:
            if mount:
                return mount
        return None

    @staticmethod
    def _sort_targets(targets: List[StorageTarget]) -> List[StorageTarget]:
        """Sort disks first, then partitions, then unallocated regions."""
        order = {
            TargetType.DISK: 0,
            TargetType.IMAGE: 1,
            TargetType.LOOP: 2,
            TargetType.PARTITION: 3,
            TargetType.UNALLOCATED: 4,
        }
        return sorted(targets, key=lambda item: (order.get(item.target_type, 9), item.name))
