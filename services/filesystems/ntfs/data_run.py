"""
NTFS non-resident attribute data-run decoding.

A non-resident attribute's content lives in one or more cluster runs, encoded
as a compact variable-length list rather than a simple array — each run's
length and (signed, delta-from-previous) LCN offset each use only as many
bytes as needed.
"""

from typing import BinaryIO, List, Optional, Tuple

from services.filesystems.ntfs.binary import read_exact


def decode_data_runs(raw: bytes) -> List[Tuple[Optional[int], int]]:
    """
    Decode a data-run byte sequence into (lcn, cluster_count) pairs.

    Args:
        raw: Bytes starting at the data-run list (runs end at a 0x00 header
            byte or the end of ``raw``, whichever comes first).

    Returns:
        Runs in VCN order. ``lcn`` is ``None`` for a sparse run (no backing
        cluster — the run represents a hole, read as zeros).
    """
    runs: List[Tuple[Optional[int], int]] = []
    offset = 0
    current_lcn = 0

    while offset < len(raw):
        header = raw[offset]
        if header == 0x00:
            break
        offset += 1

        length_size = header & 0x0F
        offset_size = (header >> 4) & 0x0F
        if offset + length_size + offset_size > len(raw):
            break

        length = int.from_bytes(raw[offset : offset + length_size], "little", signed=False)
        offset += length_size

        if offset_size == 0:
            # Sparse run: no LCN delta at all, not even a zero one.
            runs.append((None, length))
            continue

        delta = int.from_bytes(raw[offset : offset + offset_size], "little", signed=True)
        offset += offset_size
        current_lcn += delta
        runs.append((current_lcn, length))

    return runs


def resolve_runs_to_clusters(runs: List[Tuple[Optional[int], int]]) -> List[Optional[int]]:
    """
    Expand (lcn, count) runs into a flat per-cluster list in VCN order.

    Args:
        runs: Decoded data runs.

    Returns:
        One entry per cluster: an absolute LCN, or ``None`` for a sparse
        (unbacked) cluster.
    """
    clusters: List[Optional[int]] = []
    for lcn, count in runs:
        if lcn is None:
            clusters.extend([None] * count)
        else:
            clusters.extend(range(lcn, lcn + count))
    return clusters


def read_virtual_range(
    device: BinaryIO,
    clusters: List[Optional[int]],
    cluster_size: int,
    start: int,
    length: int,
) -> bytes:
    """
    Read ``length`` bytes starting at virtual byte offset ``start`` from a
    cluster-run-backed stream.

    Args:
        device: Open device/image handle.
        clusters: Flat per-cluster LCN list (see ``resolve_runs_to_clusters``).
        cluster_size: Cluster size in bytes.
        start: Virtual byte offset within the stream (e.g. within the MFT's
            own data, or within a non-resident attribute's content).
        length: Number of bytes to read.

    Returns:
        Bytes read; sparse clusters contribute zero bytes, and the stream is
        truncated at whatever ``clusters`` actually covers (a corrupt/short
        run list yields a short result rather than raising).
    """
    result = bytearray()
    remaining = length
    cluster_index = start // cluster_size
    offset_in_cluster = start % cluster_size

    while remaining > 0 and cluster_index < len(clusters):
        lcn = clusters[cluster_index]
        take = min(cluster_size - offset_in_cluster, remaining)

        if lcn is None:
            result.extend(b"\x00" * take)
        else:
            try:
                cluster_data = read_exact(device, lcn * cluster_size, cluster_size)
            except OSError:
                break
            result.extend(cluster_data[offset_in_cluster : offset_in_cluster + take])

        remaining -= take
        offset_in_cluster = 0
        cluster_index += 1

    return bytes(result)
