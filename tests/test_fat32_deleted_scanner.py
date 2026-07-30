"""
Integration tests for FAT32 deleted-file recovery.
"""

import io
import struct

import pytest

from models.storage_target import StorageTarget, TargetType
from services.filesystems.fat32.boot_sector import Fat32BootSector
from services.filesystems.fat32.deleted_scanner import Fat32DeletedScanner
from services.filesystems.fat32.directory_entry import Fat32DirEntry
from tests.fat32_helpers import (
    create_fat32_image,
    delete_file_from_image,
    make_directory,
    tools_available,
    write_file_to_image,
)

BLOCK_SIZE = 512


def _make_fat32_boot_sector() -> Fat32BootSector:
    """Build a minimal, self-consistent Fat32BootSector for synthetic tests."""
    return Fat32BootSector(
        bytes_per_sector=BLOCK_SIZE,
        sectors_per_cluster=1,
        reserved_sector_count=1,
        num_fats=1,
        fat_size_sectors=4,
        root_cluster=2,
        total_sectors=1000,
    )


def _make_device(boot_sector: Fat32BootSector, fat_entries: dict, clusters: dict) -> io.BytesIO:
    """
    Build an in-memory FAT32-shaped device: a FAT table with the given cluster ->
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


@pytest.mark.skipif(not tools_available(), reason="mkfs.vfat/mtools not available")
class TestFat32DeletedScanner:
    """Tests for deleted-file scanning."""

    def test_recovers_deleted_file_from_root_directory(self, tmp_path):
        """A deleted file directly in the root directory is recovered by content and name."""
        image = tmp_path / "deleted.fat32"
        host_file = tmp_path / "secret.txt"
        host_file.write_text("recover this deleted fat32 file", encoding="utf-8")

        create_fat32_image(image, size_mb=64)
        write_file_to_image(image, "secret.txt", host_file)
        delete_file_from_image(image, "secret.txt")

        target = StorageTarget(
            target_id="img_test",
            name="deleted.fat32",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="vfat",
        )
        scanner = Fat32DeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, remaining_queue, _processed = scanner.scan(target=target, source_target_id="img_test")

        assert len(entries) == 1
        recovered = entries[0]
        assert recovered.entry_type.value == "deleted"
        assert recovered.name.upper() == "SECRET.TXT" or recovered.name.upper().endswith("ECRET.TXT")
        assert recovered.preview_path
        assert remaining_queue == []
        with open(recovered.preview_path, encoding="utf-8") as handle:
            assert handle.read() == "recover this deleted fat32 file"

    def test_recovers_deleted_file_from_subdirectory(self, tmp_path):
        """A deleted file inside a subdirectory is found via the directory queue walk."""
        image = tmp_path / "deleted_sub.fat32"
        host_file = tmp_path / "nested.txt"
        host_file.write_text("nested deleted content", encoding="utf-8")

        create_fat32_image(image, size_mb=64)
        make_directory(image, "docs")
        write_file_to_image(image, "docs/nested.txt", host_file)
        delete_file_from_image(image, "docs/nested.txt")

        target = StorageTarget(
            target_id="img_sub_test",
            name="deleted_sub.fat32",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="vfat",
        )
        scanner = Fat32DeletedScanner(preview_dir=str(tmp_path / "previews"))
        entries, _remaining_queue, _processed = scanner.scan(
            target=target, source_target_id="img_sub_test"
        )

        assert len(entries) == 1
        with open(entries[0].preview_path, encoding="utf-8") as handle:
            assert handle.read() == "nested deleted content"

    def test_scan_returns_immediately_when_paused(self, tmp_path):
        """Pause must make scan() return right away, not block until resumed."""
        image = tmp_path / "pause.fat32"
        create_fat32_image(image, size_mb=64)

        with open(image, "rb") as device:
            from services.filesystems.fat32.boot_sector import Fat32BootSector

            root_cluster = Fat32BootSector.read_from_device(device).root_cluster

        target = StorageTarget(
            target_id="pause_test",
            name="pause.fat32",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="vfat",
        )
        scanner = Fat32DeletedScanner(preview_dir=str(tmp_path / "previews"))

        entries, remaining_queue, processed = scanner.scan(
            target=target,
            source_target_id="pause_test",
            should_pause=lambda: True,
        )

        assert entries == []
        assert remaining_queue == [root_cluster]
        assert processed == 0

    def test_supports_target_rejects_non_fat32(self, tmp_path):
        """supports_target returns False for a file that isn't a FAT32 volume."""
        image = tmp_path / "not_fat.img"
        image.write_bytes(b"\x00" * 4096)
        target = StorageTarget(
            target_id="bad",
            name="not_fat",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=4096,
        )

        assert Fat32DeletedScanner.supports_target(target) is False

    def test_supports_fat32_target(self, tmp_path):
        """supports_target detects FAT32 volumes."""
        image = tmp_path / "vol.fat32"
        create_fat32_image(image, size_mb=64)
        target = StorageTarget(
            target_id="t1",
            name="vol",
            device_path=str(image),
            target_type=TargetType.IMAGE,
            size_bytes=image.stat().st_size,
            filesystem="vfat",
        )

        assert Fat32DeletedScanner.supports_target(target) is True


class TestRecoverFileDataChainWalking:
    """
    Synthetic (non-mtools) tests proving content recovery genuinely walks the
    FAT chain when it's intact, rather than only ever assuming contiguous
    clusters — mtools' own mdel always zeros the chain, so the real-image
    integration tests above can't exercise a fragmented, chain-intact file.
    """

    def test_recovers_fragmented_file_via_intact_fat_chain(self):
        """A genuinely non-contiguous chain (10 -> 20 -> 15) must be followed in order."""
        boot_sector = _make_fat32_boot_sector()
        device = _make_device(
            boot_sector,
            fat_entries={10: 20, 20: 15, 15: 0x0FFFFFF8},
            clusters={10: b"A" * BLOCK_SIZE, 20: b"B" * BLOCK_SIZE, 15: b"C" * BLOCK_SIZE},
        )
        dir_entry = Fat32DirEntry(
            name="_RAG.BIN",
            is_directory=False,
            is_deleted=True,
            is_volume_label=False,
            is_long_name_part=False,
            first_cluster=10,
            file_size=3 * BLOCK_SIZE,
        )

        data = Fat32DeletedScanner._recover_file_data(boot_sector, device, dir_entry)

        assert data == b"A" * BLOCK_SIZE + b"B" * BLOCK_SIZE + b"C" * BLOCK_SIZE

    def test_falls_back_to_contiguous_when_chain_is_broken(self):
        """A cleared/too-short FAT chain (typical after delete) falls back to contiguous clusters."""
        boot_sector = _make_fat32_boot_sector()
        device = _make_device(
            boot_sector,
            fat_entries={10: 0},  # chain immediately ends (free), as mdel-style deletion leaves it
            clusters={10: b"D" * BLOCK_SIZE, 11: b"E" * BLOCK_SIZE, 12: b"F" * BLOCK_SIZE},
        )
        dir_entry = Fat32DirEntry(
            name="_ONTIG.BIN",
            is_directory=False,
            is_deleted=True,
            is_volume_label=False,
            is_long_name_part=False,
            first_cluster=10,
            file_size=3 * BLOCK_SIZE,
        )

        data = Fat32DeletedScanner._recover_file_data(boot_sector, device, dir_entry)

        assert data == b"D" * BLOCK_SIZE + b"E" * BLOCK_SIZE + b"F" * BLOCK_SIZE
