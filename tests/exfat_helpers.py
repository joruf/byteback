"""
Helpers for creating exFAT test images in unit tests.

Neither mtools (no exFAT support at all) nor exfat-fuse (its mount helper
hardcodes the ``allow_other`` FUSE option, which requires editing
/etc/fuse.conf as root) can write into an exFAT image unprivileged on this
platform, so files are written directly at the byte level instead — boot-
sector/FAT/directory-entry arithmetic computed independently of byteback's
own services.filesystems.exfat module, so these tests stay an honest
external check rather than a tautology — onto a volume created by the real
``mkfs.exfat`` (exfatprogs). Deletion is simulated the same way a real delete
works: clearing the InUse bit (0x80) on every entry in the file's directory-
entry set.
"""

import shutil
import struct
import subprocess
from pathlib import Path
from typing import Optional

DIR_ENTRY_SIZE = 32


def create_exfat_image(path: Path, size_mb: int = 8) -> None:
    """
    Create an empty exFAT image file.

    Args:
        path: Output image path.
        size_mb: Image size in megabytes.
    """
    subprocess.run(
        ["dd", "if=/dev/zero", f"of={path}", "bs=1M", f"count={size_mb}"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["mkfs.exfat", "-L", "testvol", str(path)], check=True, capture_output=True)


def _read_layout(path: Path) -> dict:
    """Parse the boot-sector fields needed to place directory entries by hand."""
    with open(path, "rb") as handle:
        raw = handle.read(512)

        fat_offset = struct.unpack_from("<I", raw, 80)[0]
        cluster_heap_offset = struct.unpack_from("<I", raw, 88)[0]
        cluster_count = struct.unpack_from("<I", raw, 92)[0]
        root_cluster = struct.unpack_from("<I", raw, 96)[0]
        bytes_per_sector = 1 << raw[108]
        sectors_per_cluster = 1 << raw[109]
        cluster_size = sectors_per_cluster * bytes_per_sector
        cluster_heap_start_byte = cluster_heap_offset * bytes_per_sector

        # The Allocation Bitmap's own location is given by its directory entry
        # (type 0x81) in the root directory, not by a fixed boot-sector field.
        handle.seek(cluster_heap_start_byte + (root_cluster - 2) * cluster_size)
        root_data = handle.read(cluster_size)
        bitmap_start_byte = None
        for i in range(0, len(root_data), DIR_ENTRY_SIZE):
            entry_type = root_data[i]
            if entry_type == 0x00:
                break
            if (entry_type & 0x7F) == 0x01:
                bitmap_cluster = struct.unpack_from("<I", root_data, i + 20)[0]
                bitmap_start_byte = cluster_heap_start_byte + (bitmap_cluster - 2) * cluster_size
                break
        if bitmap_start_byte is None:
            raise RuntimeError("Allocation Bitmap entry not found in test exFAT image root")

    return {
        "fat_start_byte": fat_offset * bytes_per_sector,
        "cluster_heap_start_byte": cluster_heap_start_byte,
        "cluster_size": cluster_size,
        "cluster_count": cluster_count,
        "root_cluster": root_cluster,
        "bitmap_start_byte": bitmap_start_byte,
    }


def _cluster_offset(layout: dict, cluster: int) -> int:
    return layout["cluster_heap_start_byte"] + (cluster - 2) * layout["cluster_size"]


def _mark_clusters_used(handle, layout: dict, clusters: list) -> None:
    """Set each cluster's bit in the Allocation Bitmap (bit 0 = cluster 2, LSB-first)."""
    for cluster in clusters:
        bit_index = cluster - 2
        pos = layout["bitmap_start_byte"] + bit_index // 8
        handle.seek(pos)
        current = handle.read(1)[0]
        handle.seek(pos)
        handle.write(bytes([current | (1 << (bit_index % 8))]))


def _entry_set_checksum(entries: bytes) -> int:
    """
    Compute an exFAT directory-entry-set checksum (bytes 2-3 of the primary
    entry are skipped, per spec, since that's where the checksum itself lives).
    """
    checksum = 0
    for i, byte in enumerate(entries):
        if i in (2, 3):
            continue
        checksum = ((checksum << 15) | (checksum >> 1)) & 0xFFFF
        checksum = (checksum + byte) & 0xFFFF
    return checksum


def _name_hash(name: str) -> int:
    """
    Compute an exFAT NameHash (over the up-cased name) as fsck.exfat expects.

    Uses Python's ``str.upper()`` rather than parsing the on-disk Upcase
    Table — equivalent to the default table for the plain-ASCII names these
    tests use.
    """
    upcased_utf16 = name.upper().encode("utf-16-le")
    hash_value = 0
    for byte in upcased_utf16:
        hash_value = ((hash_value << 15) | (hash_value >> 1)) & 0xFFFF
        hash_value = (hash_value + byte) & 0xFFFF
    return hash_value


def _find_free_clusters(handle, layout: dict, count: int) -> list:
    """Scan the FAT for ``count`` free (0x00000000) clusters."""
    found = []
    for cluster in range(2, 2 + layout["cluster_count"]):
        handle.seek(layout["fat_start_byte"] + cluster * 4)
        if struct.unpack("<I", handle.read(4))[0] == 0:
            found.append(cluster)
            if len(found) == count:
                return found
    raise RuntimeError("Not enough free clusters in test exFAT image")


def _iter_entries(data: bytes):
    """Yield (name, is_directory, first_cluster, data_length, set_start_offset) per file entry."""
    i = 0
    while i < len(data):
        entry_type = data[i]
        if entry_type == 0x00:
            break
        if (entry_type & 0x7F) != 0x05:
            i += DIR_ENTRY_SIZE
            continue

        secondary_count = data[i + 1]
        set_size = (1 + secondary_count) * DIR_ENTRY_SIZE
        if secondary_count < 1 or i + set_size > len(data):
            i += DIR_ENTRY_SIZE
            continue

        stream_off = i + DIR_ENTRY_SIZE
        file_attributes = struct.unpack_from("<H", data, i + 4)[0]
        is_directory = bool(file_attributes & 0x10)
        name_length = data[stream_off + 3]
        first_cluster = struct.unpack_from("<I", data, stream_off + 20)[0]
        data_length = struct.unpack_from("<Q", data, stream_off + 24)[0]

        name_bytes = bytearray()
        remaining = name_length
        for j in range(secondary_count - 1):
            name_off = stream_off + (1 + j) * DIR_ENTRY_SIZE
            take = min(15, remaining)
            name_bytes += data[name_off + 2 : name_off + 2 + take * 2]
            remaining -= take

        name = bytes(name_bytes).decode("utf-16-le", errors="ignore")
        yield name, is_directory, first_cluster, data_length, i
        i += set_size


def _find_directory_cluster(handle, layout: dict, dirname: str) -> int:
    """Find a root-level subdirectory's first cluster by name."""
    handle.seek(_cluster_offset(layout, layout["root_cluster"]))
    data = handle.read(layout["cluster_size"])
    for name, is_dir, first_cluster, _size, _offset in _iter_entries(data):
        if is_dir and name == dirname:
            return first_cluster
    raise RuntimeError(f"Directory {dirname!r} not found in test exFAT image")


def _write_entry_set(
    handle,
    layout: dict,
    parent_cluster: int,
    name: str,
    is_directory: bool,
    payload: bytes,
) -> None:
    """Allocate clusters for ``payload``, write it, and append a directory-entry set for it."""
    cluster_size = layout["cluster_size"]
    needed_clusters = max(1, -(-len(payload) // cluster_size))
    name_entries_needed = max(1, -(-len(name) // 15))
    secondary_count = 1 + name_entries_needed
    needed_slots = 1 + secondary_count

    clusters = _find_free_clusters(handle, layout, needed_clusters)
    _mark_clusters_used(handle, layout, clusters)

    for index, cluster in enumerate(clusters):
        next_value = clusters[index + 1] if index + 1 < len(clusters) else 0xFFFFFFFF
        handle.seek(layout["fat_start_byte"] + cluster * 4)
        handle.write(struct.pack("<I", next_value))

    for index, cluster in enumerate(clusters):
        chunk = payload[index * cluster_size : (index + 1) * cluster_size]
        chunk = chunk.ljust(cluster_size, b"\x00")
        handle.seek(_cluster_offset(layout, cluster))
        handle.write(chunk)

    parent_offset = _cluster_offset(layout, parent_cluster)
    handle.seek(parent_offset)
    parent_data = handle.read(cluster_size)

    free_run = 0
    slot_index = None
    for i in range(0, len(parent_data), DIR_ENTRY_SIZE):
        if parent_data[i] == 0x00:
            free_run += 1
            if free_run >= needed_slots:
                slot_index = i - (needed_slots - 1) * DIR_ENTRY_SIZE
                break
        else:
            free_run = 0
    if slot_index is None:
        raise RuntimeError("No free directory-entry run found in test exFAT image")

    file_entry = bytearray(DIR_ENTRY_SIZE)
    file_entry[0] = 0x85
    file_entry[1] = secondary_count
    struct.pack_into("<H", file_entry, 4, 0x10 if is_directory else 0x20)

    stream_entry = bytearray(DIR_ENTRY_SIZE)
    stream_entry[0] = 0xC0
    stream_entry[1] = 0x01  # AllocationPossible; a real FAT chain is written above (no NoFatChain)
    stream_entry[3] = len(name)
    struct.pack_into("<H", stream_entry, 4, _name_hash(name))
    struct.pack_into("<I", stream_entry, 20, clusters[0])
    # A directory's DataLength is its allocated space (always a whole number
    # of clusters); a file's is its exact content length, independent of the
    # padding in its last cluster.
    stored_length = needed_clusters * cluster_size if is_directory else len(payload)
    struct.pack_into("<Q", stream_entry, 24, stored_length)

    entries = [file_entry, bytearray(stream_entry)]
    name_utf16 = name.encode("utf-16-le")
    for i in range(name_entries_needed):
        name_entry = bytearray(DIR_ENTRY_SIZE)
        name_entry[0] = 0xC1
        chunk = name_utf16[i * 30 : (i + 1) * 30]
        name_entry[2 : 2 + len(chunk)] = chunk
        entries.append(name_entry)

    checksum = _entry_set_checksum(b"".join(bytes(e) for e in entries))
    struct.pack_into("<H", file_entry, 2, checksum)

    handle.seek(parent_offset + slot_index)
    handle.write(b"".join(bytes(e) for e in entries))


def write_file_to_image(
    image_path: Path, image_filename: str, host_file: Path, parent: Optional[str] = None
) -> None:
    """
    Write a file directly into an exFAT image (no mount).

    Args:
        image_path: exFAT image path.
        image_filename: Destination filename (basename only).
        host_file: Local file to copy in.
        parent: Optional subdirectory name (a direct child of root) to write
            into instead of the root directory — must already exist, see
            ``make_directory``.
    """
    payload = host_file.read_bytes()
    with open(image_path, "r+b") as handle:
        layout = _read_layout(image_path)
        parent_cluster = (
            _find_directory_cluster(handle, layout, parent) if parent is not None else layout["root_cluster"]
        )
        _write_entry_set(handle, layout, parent_cluster, image_filename, False, payload)


def make_directory(image_path: Path, dirname: str) -> None:
    """Create an empty subdirectory directly in an exFAT image's root (no mount)."""
    with open(image_path, "r+b") as handle:
        layout = _read_layout(image_path)
        _write_entry_set(handle, layout, layout["root_cluster"], dirname, True, b"")


def delete_file_from_image(image_path: Path, image_filename: str, parent: Optional[str] = None) -> None:
    """
    Clear the InUse bit on a file's directory-entry set, simulating a real delete.

    Args:
        image_path: exFAT image path.
        image_filename: Filename to delete.
        parent: Optional subdirectory name (a direct child of root) the file
            lives in, instead of the root directory.
    """
    with open(image_path, "r+b") as handle:
        layout = _read_layout(image_path)
        dir_cluster = (
            _find_directory_cluster(handle, layout, parent) if parent is not None else layout["root_cluster"]
        )

        offset = _cluster_offset(layout, dir_cluster)
        handle.seek(offset)
        data = bytearray(handle.read(layout["cluster_size"]))

        for name, _is_dir, _first_cluster, _size, set_start in _iter_entries(bytes(data)):
            if name != image_filename:
                continue
            secondary_count = data[set_start + 1]
            for slot in range(1 + secondary_count):
                data[set_start + slot * DIR_ENTRY_SIZE] &= 0x7F
            handle.seek(offset)
            handle.write(bytes(data))
            return

    raise RuntimeError(f"File {image_filename!r} not found in test exFAT image")


def tools_available() -> bool:
    """Return True when mkfs.exfat is available."""
    return shutil.which("mkfs.exfat") is not None
