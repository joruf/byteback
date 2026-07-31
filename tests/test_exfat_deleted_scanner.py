"""
Integration tests for exFAT deleted-file recovery.
"""

import io
import struct

import pytest

from models.storage_target import StorageTarget, TargetType
from services.filesystems.exfat.boot_sector import ExfatBootSector
from services.filesystems.exfat.deleted_scanner import ExfatDeletedScanner
from services.filesystems.exfat.directory_entry import ExfatDirEntry
from tests.exfat_helpers import (
    create_exfat_image,
    delete_file_from_image,
    make_directory,
    tools_available,
    write_file_to_image,
)

BLOCK_SIZE = 512


def _make_exfat_boot_sector() -> ExfatBootSector:
    """Build a minimal, self-consistent ExfatBootSector for synthetic tests."""
    return ExfatBootSector(
        bytes_per_sector=BLOCK_SIZE,
        sectors_per_cluster=1,
        fat_offset_sectors=1,
        cluster_heap_offset_sectors=16,
        cluster_count=1000,
        root_cluster=2,
    )


def _make_device(boot_sector: ExfatBootSector, fat_entries: dict, clusters: dict) -> io.BytesIO:
    """
    Build an in-memory exFAT-shaped device: a FAT table with the given cluster ->
    next-cluster entries, and cluster data payloads.
    """
    highest_cluster = max(clusters) if clusters else boot_sector.root_cluster
    total_size = boot_sector.cluster_to_byte_offset(highest_cluster + 1)
    buffer = bytearray(total_size)

    for cluster, next_cluster in fat_entries.items():
        offset = boot_sector.fat_start_byte + cluster * 4
        struct.pack_into("<I", buffer, offset, next_cluster)

    for cluster, data in clusters.items():
        offset = boot_sector.cluster_to_byte_offset(cluster)
        buffer[offset : offset + len(data)] = data.ljust(boot_sector.cluster_size, b"\x00")

    return io.BytesIO(bytes(buffer))


@pytest.mark.skipif(not tools_available(), reason="mkfs.exfat not available")
class TestExfatDeletedScanner:
    """Tests for deleted-file scanning."""

    def test_recovers_deleted_file_from_root_directory(self, tmp_path):
        """A deleted file directly in the root directory is recovered by content and name."""
        image = tmp_path / "deleted.exfat"
        host_file = tmp_path / "secret.txt"
        host_file.write_text("recover this deleted exfat file", encoding="utf-8")

        create_exfat_image(image, size_mb=8)
        write_file_to_image(image, "secret.txt", host_file)
        delete_file_from_image(image, "secret.txt")

        target = StorageTarget(
            target_id="img_test",
            name="deleted.exfat",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="exfat",
        )
        scanner = ExfatDeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, remaining_queue, _processed = scanner.scan(target=target, source_target_id="img_test")

        assert len(entries) == 1
        recovered = entries[0]
        assert recovered.entry_type.value == "deleted"
        assert recovered.name == "secret.txt"
        assert recovered.preview_path
        assert remaining_queue == []
        with open(recovered.preview_path, encoding="utf-8") as handle:
            assert handle.read() == "recover this deleted exfat file"

    def test_recovers_deleted_file_from_subdirectory(self, tmp_path):
        """A deleted file inside a subdirectory is found via the directory queue walk."""
        image = tmp_path / "deleted_sub.exfat"
        host_file = tmp_path / "nested.txt"
        host_file.write_text("nested deleted content", encoding="utf-8")

        create_exfat_image(image, size_mb=8)
        make_directory(image, "docs")
        write_file_to_image(image, "nested.txt", host_file, parent="docs")
        delete_file_from_image(image, "nested.txt", parent="docs")

        target = StorageTarget(
            target_id="img_sub_test",
            name="deleted_sub.exfat",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="exfat",
        )
        scanner = ExfatDeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, _remaining_queue, _processed = scanner.scan(
            target=target, source_target_id="img_sub_test"
        )

        assert len(entries) == 1
        with open(entries[0].preview_path, encoding="utf-8") as handle:
            assert handle.read() == "nested deleted content"

    def test_scan_returns_immediately_when_paused(self, tmp_path):
        """Pause must make scan() return right away, not block until resumed."""
        image = tmp_path / "pause.exfat"
        create_exfat_image(image, size_mb=8)

        with open(image, "rb") as device:
            root_cluster = ExfatBootSector.read_from_device(device).root_cluster

        target = StorageTarget(
            target_id="pause_test",
            name="pause.exfat",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="exfat",
        )
        scanner = ExfatDeletedScanner(preview_dir=str(tmp_path / "previews"))

        entries, remaining_queue, processed = scanner.scan(
            target=target,
            source_target_id="pause_test",
            should_pause=lambda: True,
        )

        assert entries == []
        assert remaining_queue == [ExfatDeletedScanner._encode_queue_item(root_cluster, None, False)]
        assert processed == 0

    def test_supports_target_rejects_non_exfat(self, tmp_path):
        """supports_target returns False for a file that isn't an exFAT volume."""
        image = tmp_path / "not_exfat.img"
        image.write_bytes(b"\x00" * 4096)
        target = StorageTarget(
            target_id="bad",
            name="not_exfat",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=4096,
        )

        assert ExfatDeletedScanner.supports_target(target) is False

    def test_supports_exfat_target(self, tmp_path):
        """supports_target detects exFAT volumes."""
        image = tmp_path / "vol.exfat"
        create_exfat_image(image, size_mb=8)
        target = StorageTarget(
            target_id="t1",
            name="vol",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="exfat",
        )

        assert ExfatDeletedScanner.supports_target(target) is True


class TestQueueItemEncoding:
    """Tests for the directory-descriptor checkpoint encoding."""

    def test_round_trips_root_directory_marker(self):
        encoded = ExfatDeletedScanner._encode_queue_item(2, None, False)

        assert ExfatDeletedScanner._decode_queue_item(encoded) == (2, None, False)

    def test_round_trips_subdirectory_descriptor(self):
        encoded = ExfatDeletedScanner._encode_queue_item(8, 4096, True)

        assert ExfatDeletedScanner._decode_queue_item(encoded) == (8, 4096, True)


class TestRecoverFileDataChainWalking:
    """
    Synthetic (non-mkfs.exfat) tests proving content recovery genuinely walks
    the FAT chain when it's intact and never consults the FAT at all for a
    ``no_fat_chain`` file — the real-image integration tests above always
    write a normal FAT chain, so they can't exercise either of these paths.
    """

    def test_recovers_fragmented_file_via_intact_fat_chain(self):
        """A genuinely non-contiguous chain (10 -> 20 -> 15) must be followed in order."""
        boot_sector = _make_exfat_boot_sector()
        device = _make_device(
            boot_sector,
            fat_entries={10: 20, 20: 15, 15: 0xFFFFFFFF},
            clusters={10: b"A" * BLOCK_SIZE, 20: b"B" * BLOCK_SIZE, 15: b"C" * BLOCK_SIZE},
        )
        dir_entry = ExfatDirEntry(
            name="frag.bin",
            is_directory=False,
            is_deleted=True,
            first_cluster=10,
            data_length=3 * BLOCK_SIZE,
            no_fat_chain=False,
        )

        data = ExfatDeletedScanner._recover_file_data(boot_sector, device, dir_entry)

        assert data == b"A" * BLOCK_SIZE + b"B" * BLOCK_SIZE + b"C" * BLOCK_SIZE

    def test_falls_back_to_contiguous_when_chain_is_broken(self):
        """A cleared/too-short FAT chain (typical after delete) falls back to contiguous clusters."""
        boot_sector = _make_exfat_boot_sector()
        device = _make_device(
            boot_sector,
            fat_entries={10: 0},  # chain immediately ends (free), as a cleared post-delete chain would
            clusters={10: b"D" * BLOCK_SIZE, 11: b"E" * BLOCK_SIZE, 12: b"F" * BLOCK_SIZE},
        )
        dir_entry = ExfatDirEntry(
            name="contig.bin",
            is_directory=False,
            is_deleted=True,
            first_cluster=10,
            data_length=3 * BLOCK_SIZE,
            no_fat_chain=False,
        )

        data = ExfatDeletedScanner._recover_file_data(boot_sector, device, dir_entry)

        assert data == b"D" * BLOCK_SIZE + b"E" * BLOCK_SIZE + b"F" * BLOCK_SIZE

    def test_no_fat_chain_file_ignores_the_fat_entirely(self):
        """
        A ``no_fat_chain`` file's clusters were never given FAT entries at all —
        even a FAT that (if followed) would point somewhere else must be
        ignored, reading contiguous clusters from first_cluster unconditionally.
        """
        boot_sector = _make_exfat_boot_sector()
        device = _make_device(
            boot_sector,
            fat_entries={10: 999},  # garbage/unpopulated FAT entry, must not be followed
            clusters={10: b"G" * BLOCK_SIZE, 11: b"H" * BLOCK_SIZE},
        )
        dir_entry = ExfatDirEntry(
            name="nofatchain.bin",
            is_directory=False,
            is_deleted=True,
            first_cluster=10,
            data_length=2 * BLOCK_SIZE,
            no_fat_chain=True,
        )

        data = ExfatDeletedScanner._recover_file_data(boot_sector, device, dir_entry)

        assert data == b"G" * BLOCK_SIZE + b"H" * BLOCK_SIZE
