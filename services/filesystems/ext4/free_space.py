"""
Map and scan unallocated ext4 blocks (free space only).
"""

import logging
from typing import Callable, List, Optional, Tuple

from models.recovery_entry import RecoveryEntry
from models.storage_target import StorageTarget
from services.carving.file_carver import FileCarver
from services.filesystems.ext4.binary import block_ranges_to_byte_ranges, read_block
from services.filesystems.ext4.superblock import Ext4Superblock
from utils.device_io import open_device

logger = logging.getLogger(__name__)


class Ext4FreeSpaceScanner:
    """
    Carve only free (unallocated) blocks inside an ext4 partition.

    Reads block bitmaps from each block group and runs signature carving on
    contiguous free block ranges.
    """

    def __init__(self) -> None:
        """Initialize the carving backend."""
        self._carver = FileCarver()

    def scan(
        self,
        target: StorageTarget,
        source_target_id: str,
        start_range_index: int = 0,
        deep_scan: bool = True,
        on_entry: Optional[Callable[[RecoveryEntry], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_pause: Optional[Callable[[], bool]] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Tuple[List[RecoveryEntry], int, int]:
        """
        Scan all free block ranges inside an ext4 filesystem.

        Args:
            target: ext4 partition or image target.
            source_target_id: ID stored on each RecoveryEntry.
            start_range_index: Resume from this free-range index.
            deep_scan: When False, use quick carving signatures only.
            on_entry: Callback for each carved file.
            on_progress: Callback(bytes_done, total_bytes, description).
            should_pause: Returns True when scanning should pause.
            should_cancel: Returns True when scanning should abort.

        Returns:
            Tuple of (entries, bytes_processed, final_range_index).
        """
        entries: List[RecoveryEntry] = []
        device_path = target.device_path

        with open_device(device_path) as device:
            superblock = Ext4Superblock.read_from_device(device)
            block_ranges = self._collect_free_block_ranges(device, superblock)
            byte_ranges = block_ranges_to_byte_ranges(block_ranges, superblock.block_size)
            total_bytes = sum(length for _offset, length in byte_ranges)
            processed_global = 0

            for range_index, (byte_offset, byte_length) in enumerate(byte_ranges):
                if range_index < start_range_index:
                    processed_global += byte_length
                    continue

                if should_cancel and should_cancel():
                    return entries, processed_global, range_index

                while should_pause and should_pause():
                    if should_cancel and should_cancel():
                        return entries, processed_global, range_index
                    import time

                    time.sleep(0.2)

                carve_target = StorageTarget(
                    target_id=target.target_id,
                    name=target.name,
                    device_path=device_path,
                    target_type=target.target_type,
                    size_bytes=byte_length,
                    start_offset=byte_offset,
                    filesystem=target.filesystem,
                    description=f"Free space @ {byte_offset}",
                )

                range_entries, _final = self._carver.carve_range(
                    target=carve_target,
                    start_offset=byte_offset,
                    size_bytes=byte_length,
                    source_target_id=source_target_id,
                    deep_scan=deep_scan,
                    on_entry=on_entry,
                    on_progress=None,
                    should_pause=should_pause,
                    should_cancel=should_cancel,
                )
                entries.extend(range_entries)
                processed_global += byte_length

                if on_progress:
                    on_progress(
                        processed_global,
                        total_bytes,
                        f"Free space range {range_index + 1}/{len(byte_ranges)}",
                    )

        return entries, processed_global, len(byte_ranges)

    def _collect_free_block_ranges(self, device, superblock: Ext4Superblock) -> List[Tuple[int, int]]:
        """
        Parse block bitmaps and return contiguous free block runs.

        Returns:
            List of (start_block, block_count) tuples in global block numbers.
        """
        groups = superblock.load_group_descriptors(device)
        ranges: List[Tuple[int, int]] = []

        for group in groups:
            if group.free_blocks == 0:
                continue

            try:
                bitmap = read_block(device, superblock.block_size, group.block_bitmap_block)
            except OSError as exc:
                logger.debug("Could not read block bitmap for group %s: %s", group.index, exc)
                continue

            global_base = group.index * superblock.blocks_per_group
            ranges.extend(self._parse_bitmap(bitmap, global_base, superblock.blocks_per_group))

        return ranges

    @staticmethod
    def _parse_bitmap(bitmap: bytes, global_base: int, blocks_in_group: int) -> List[Tuple[int, int]]:
        """Find contiguous free block runs in one block-group bitmap."""
        ranges: List[Tuple[int, int]] = []
        run_start = None
        run_length = 0

        for local_index in range(blocks_in_group):
            byte_index = local_index // 8
            bit_index = local_index % 8
            if byte_index >= len(bitmap):
                break
            allocated = bitmap[byte_index] & (1 << bit_index)
            if not allocated:
                if run_start is None:
                    run_start = global_base + local_index
                    run_length = 1
                else:
                    run_length += 1
            elif run_start is not None:
                ranges.append((run_start, run_length))
                run_start = None
                run_length = 0

        if run_start is not None:
            ranges.append((run_start, run_length))

        return ranges

    @staticmethod
    def supports_target(target: StorageTarget) -> bool:
        """Return True when the target is a readable ext4 volume."""
        if target.filesystem and "ext" in target.filesystem.lower():
            return True
        try:
            with open_device(target.device_path) as device:
                Ext4Superblock.read_from_device(device)
            return True
        except (OSError, ValueError):
            return False
