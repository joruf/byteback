"""
Unit tests for NTFS data-run decoding.

The two byte sequences below are not made up — they were captured from real
mkntfs/ntfscp-created images while verifying this module's byte layout, so
these tests also pin down a real signed-offset decoding bug caught during
that process (a naive same-order read of the offset bytes silently produced
the wrong LCN).
"""

from services.filesystems.ntfs.data_run import (
    decode_data_runs,
    read_virtual_range,
    resolve_runs_to_clusters,
)


class TestDecodeDataRuns:
    """Tests for decoding the compact (header, length, signed-offset) run format."""

    def test_single_small_run(self):
        """$MFT's own $DATA on a fresh 64MB volume: 7 clusters starting at LCN 4."""
        raw = bytes.fromhex("11070400")

        runs = decode_data_runs(raw)

        assert runs == [(4, 7)]

    def test_run_with_two_byte_signed_offset(self):
        """A larger positive LCN needs a 2-byte offset field, decoded little-endian."""
        raw = bytes.fromhex("2104002200")

        runs = decode_data_runs(raw)

        assert runs == [(8704, 4)]

    def test_multiple_runs_accumulate_lcn_as_a_delta(self):
        """The second run's offset is relative to the first run's LCN, not absolute."""
        raw = bytes.fromhex("11050a") + bytes.fromhex("1103f6") + b"\x00"
        # Run 1: length=5, delta=+10 -> LCN 10
        # Run 2: length=3, delta=-10 (0xF6 as signed byte) -> LCN 0

        runs = decode_data_runs(raw)

        assert runs == [(10, 5), (0, 3)]

    def test_sparse_run_has_no_lcn(self):
        """An offset field size of 0 marks a sparse (hole) run with no backing LCN."""
        raw = bytes.fromhex("0105")  # length_size=1, offset_size=0, length=5

        runs = decode_data_runs(raw)

        assert runs == [(None, 5)]

    def test_stops_at_terminator_byte(self):
        raw = bytes.fromhex("1107040000000000")

        runs = decode_data_runs(raw)

        assert runs == [(4, 7)]


class TestResolveRunsAndReadVirtualRange:
    """Tests for expanding runs and reading a run-backed virtual byte stream."""

    def test_resolve_runs_to_clusters_expands_each_run(self):
        clusters = resolve_runs_to_clusters([(10, 3), (None, 2), (50, 1)])

        assert clusters == [10, 11, 12, None, None, 50]

    def test_read_virtual_range_reads_across_clusters(self, tmp_path):
        cluster_size = 16
        image = tmp_path / "device.bin"
        buffer = bytearray(200 * cluster_size)
        buffer[10 * cluster_size : 10 * cluster_size + cluster_size] = b"A" * cluster_size
        buffer[11 * cluster_size : 11 * cluster_size + cluster_size] = b"B" * cluster_size
        image.write_bytes(bytes(buffer))

        clusters = [10, 11]
        with open(image, "rb") as device:
            data = read_virtual_range(device, clusters, cluster_size, start=8, length=20)

        assert data == b"A" * (cluster_size - 8) + b"B" * 12

    def test_read_virtual_range_zero_fills_sparse_clusters(self, tmp_path):
        cluster_size = 16
        image = tmp_path / "device.bin"
        buffer = bytearray(200 * cluster_size)
        buffer[10 * cluster_size : 10 * cluster_size + cluster_size] = b"A" * cluster_size
        image.write_bytes(bytes(buffer))

        clusters = [10, None]
        with open(image, "rb") as device:
            data = read_virtual_range(device, clusters, cluster_size, start=0, length=32)

        assert data == b"A" * cluster_size + b"\x00" * cluster_size
