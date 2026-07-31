"""
Unit tests for exFAT directory-entry-set parsing.
"""

import io
import struct

from services.filesystems.exfat.boot_sector import ExfatBootSector
from services.filesystems.exfat.directory_entry import list_directory_entries

BLOCK_SIZE = 512


def _boot_sector() -> ExfatBootSector:
    """Minimal, self-consistent ExfatBootSector for synthetic tests."""
    return ExfatBootSector(
        bytes_per_sector=BLOCK_SIZE,
        sectors_per_cluster=1,
        fat_offset_sectors=1,
        cluster_heap_offset_sectors=16,
        cluster_count=1000,
        root_cluster=2,
    )


def _build_entry_set(
    name: str,
    is_directory: bool = False,
    in_use: bool = True,
    first_cluster: int = 0,
    data_length: int = 0,
    no_fat_chain: bool = False,
) -> bytes:
    """Build one raw File + Stream Extension + File Name entry-set for tests."""
    name_entries_needed = max(1, -(-len(name) // 15))
    secondary_count = 1 + name_entries_needed
    in_use_bit = 0x80 if in_use else 0x00

    file_entry = bytearray(32)
    file_entry[0] = 0x05 | in_use_bit
    file_entry[1] = secondary_count
    struct.pack_into("<H", file_entry, 4, 0x10 if is_directory else 0x20)

    stream_entry = bytearray(32)
    stream_entry[0] = 0x40 | in_use_bit
    stream_entry[1] = 0x01 | (0x02 if no_fat_chain else 0x00)
    stream_entry[3] = len(name)
    struct.pack_into("<I", stream_entry, 20, first_cluster)
    struct.pack_into("<Q", stream_entry, 24, data_length)

    parts = [bytes(file_entry), bytes(stream_entry)]
    name_utf16 = name.encode("utf-16-le")
    for i in range(name_entries_needed):
        name_entry = bytearray(32)
        name_entry[0] = 0x41 | in_use_bit
        chunk = name_utf16[i * 30 : (i + 1) * 30]
        name_entry[2 : 2 + len(chunk)] = chunk
        parts.append(bytes(name_entry))

    return b"".join(parts)


def _make_root_device(boot_sector: ExfatBootSector, root_data: bytes) -> io.BytesIO:
    """Build an in-memory device with ``root_data`` as the (single-cluster) root directory."""
    offset = boot_sector.cluster_to_byte_offset(boot_sector.root_cluster)
    buffer = bytearray(offset + boot_sector.cluster_size)
    buffer[offset : offset + len(root_data)] = root_data
    # A FAT entry marking the root cluster as end-of-chain, so walk_cluster_chain
    # (used when data_length=None) terminates after this one cluster.
    fat_offset = boot_sector.fat_start_byte + boot_sector.root_cluster * 4
    if fat_offset + 4 > len(buffer):
        buffer.extend(b"\x00" * (fat_offset + 4 - len(buffer)))
    struct.pack_into("<I", buffer, fat_offset, 0xFFFFFFFF)
    return io.BytesIO(bytes(buffer))


class TestListDirectoryEntries:
    """Tests for parsing a directory's file entry sets."""

    def test_parses_regular_in_use_file(self):
        boot_sector = _boot_sector()
        data = _build_entry_set("secret.txt", first_cluster=5, data_length=100)
        device = _make_root_device(boot_sector, data)

        entries = list_directory_entries(boot_sector, device, boot_sector.root_cluster)

        assert len(entries) == 1
        entry = entries[0]
        assert entry.name == "secret.txt"
        assert entry.is_directory is False
        assert entry.is_deleted is False
        assert entry.first_cluster == 5
        assert entry.data_length == 100
        assert entry.no_fat_chain is False

    def test_deleted_entry_keeps_name_and_metadata(self):
        """Deletion clears InUse on every slot but the set stays fully readable."""
        boot_sector = _boot_sector()
        data = _build_entry_set("secret.txt", in_use=False, first_cluster=5, data_length=100)
        device = _make_root_device(boot_sector, data)

        entries = list_directory_entries(boot_sector, device, boot_sector.root_cluster)

        assert len(entries) == 1
        assert entries[0].is_deleted is True
        assert entries[0].name == "secret.txt"

    def test_directory_attribute_and_no_fat_chain_flag(self):
        boot_sector = _boot_sector()
        data = _build_entry_set("docs", is_directory=True, first_cluster=8, data_length=4096, no_fat_chain=True)
        device = _make_root_device(boot_sector, data)

        entries = list_directory_entries(boot_sector, device, boot_sector.root_cluster)

        assert entries[0].is_directory is True
        assert entries[0].no_fat_chain is True

    def test_long_name_spans_multiple_name_entries(self):
        """A name longer than 15 UTF-16 units needs more than one File Name entry."""
        long_name = "a" * 20 + ".txt"  # 24 chars, needs 2 File Name entries
        boot_sector = _boot_sector()
        data = _build_entry_set(long_name, first_cluster=3, data_length=1)
        device = _make_root_device(boot_sector, data)

        entries = list_directory_entries(boot_sector, device, boot_sector.root_cluster)

        assert entries[0].name == long_name

    def test_stops_at_end_of_directory_marker(self):
        """A zero EntryType means no more entries follow, even if bytes remain after it."""
        boot_sector = _boot_sector()
        first = _build_entry_set("first.txt", first_cluster=3, data_length=1)
        second = _build_entry_set("second.txt", first_cluster=4, data_length=1)
        data = first + b"\x00" * 32 + second
        device = _make_root_device(boot_sector, data)

        entries = list_directory_entries(boot_sector, device, boot_sector.root_cluster)

        assert [e.name for e in entries] == ["first.txt"]

    def test_non_file_entries_are_skipped(self):
        """Allocation Bitmap (0x81) and Upcase Table (0x82) entries are not files."""
        bitmap_entry = bytearray(32)
        bitmap_entry[0] = 0x81
        upcase_entry = bytearray(32)
        upcase_entry[0] = 0x82
        boot_sector = _boot_sector()
        file_data = _build_entry_set("secret.txt", first_cluster=5, data_length=100)
        data = bytes(bitmap_entry) + bytes(upcase_entry) + file_data
        device = _make_root_device(boot_sector, data)

        entries = list_directory_entries(boot_sector, device, boot_sector.root_cluster)

        assert [e.name for e in entries] == ["secret.txt"]
