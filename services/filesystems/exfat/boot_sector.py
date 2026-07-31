"""
exFAT boot sector (Main Boot Record) parsing and cluster/FAT-chain arithmetic.
"""

from dataclasses import dataclass
from typing import BinaryIO, List

from services.filesystems.exfat.binary import (
    EXFAT_BOOT_SIGNATURE,
    EXFAT_BOOT_SIGNATURE_OFFSET,
    EXFAT_OEM_ID,
    FAT_BAD_CLUSTER,
    FAT_FREE_CLUSTER,
    FAT_MIN_EOC,
    read_exact,
    read_le16,
    read_le32,
)

# Safety cap on cluster-chain length, independent of any caller-supplied budget —
# guards against a corrupt/cyclic FAT sending a walk on indefinitely.
MAX_CHAIN_CLUSTERS = 2_000_000


@dataclass
class ExfatBootSector:
    """
    Parsed exFAT boot sector fields required for recovery scanning.

    Attributes:
        bytes_per_sector: Sector size in bytes (1 << BytesPerSectorShift).
        sectors_per_cluster: Cluster size in sectors (1 << SectorsPerClusterShift).
        fat_offset_sectors: Sector offset of the first FAT, from the volume start.
        cluster_heap_offset_sectors: Sector offset of cluster 2, from the volume start.
        cluster_count: Total number of clusters in the cluster heap.
        root_cluster: Starting cluster of the root directory.
    """

    bytes_per_sector: int
    sectors_per_cluster: int
    fat_offset_sectors: int
    cluster_heap_offset_sectors: int
    cluster_count: int
    root_cluster: int

    @classmethod
    def read_from_device(cls, device: BinaryIO) -> "ExfatBootSector":
        """
        Parse the boot sector from a block device or image file.

        Args:
            device: Open binary handle positioned at the start of the volume.

        Returns:
            Parsed ExfatBootSector instance.

        Raises:
            ValueError: When the volume is not a valid exFAT filesystem.
        """
        raw = read_exact(device, 0, 512)

        if read_le16(raw, EXFAT_BOOT_SIGNATURE_OFFSET) != EXFAT_BOOT_SIGNATURE:
            raise ValueError("Not an exFAT volume (missing 0xAA55 boot signature)")
        if raw[3:11] != EXFAT_OEM_ID:
            raise ValueError("Not an exFAT volume (missing 'EXFAT   ' OEM id)")

        fat_offset_sectors = read_le32(raw, 80)
        cluster_heap_offset_sectors = read_le32(raw, 88)
        cluster_count = read_le32(raw, 92)
        root_cluster = read_le32(raw, 96)
        bytes_per_sector_shift = raw[108]
        sectors_per_cluster_shift = raw[109]

        if cluster_count == 0 or root_cluster < 2:
            raise ValueError("Invalid exFAT boot sector: zero cluster count or root cluster")

        return cls(
            bytes_per_sector=1 << bytes_per_sector_shift,
            sectors_per_cluster=1 << sectors_per_cluster_shift,
            fat_offset_sectors=fat_offset_sectors,
            cluster_heap_offset_sectors=cluster_heap_offset_sectors,
            cluster_count=cluster_count,
            root_cluster=root_cluster,
        )

    @property
    def cluster_size(self) -> int:
        """Cluster size in bytes."""
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def fat_start_byte(self) -> int:
        """Absolute byte offset of the first FAT."""
        return self.fat_offset_sectors * self.bytes_per_sector

    def cluster_to_byte_offset(self, cluster: int) -> int:
        """
        Convert a cluster number to its absolute byte offset in the volume.

        Args:
            cluster: Cluster number (valid data clusters start at 2).

        Returns:
            Absolute byte offset of the start of that cluster.
        """
        cluster_heap_start = self.cluster_heap_offset_sectors * self.bytes_per_sector
        return cluster_heap_start + (cluster - 2) * self.cluster_size

    def read_fat_entry(self, device: BinaryIO, cluster: int) -> int:
        """
        Read one 32-bit FAT entry.

        Args:
            device: Open device/image handle.
            cluster: Cluster number to look up.

        Returns:
            FAT entry value (0 = free, >= FAT_MIN_EOC = end of chain).
        """
        offset = self.fat_start_byte + cluster * 4
        raw = read_exact(device, offset, 4)
        return read_le32(raw, 0)

    def read_cluster_chain_data(
        self,
        device: BinaryIO,
        start_cluster: int,
        max_clusters: int = MAX_CHAIN_CLUSTERS,
    ) -> bytes:
        """
        Follow the FAT chain from ``start_cluster`` and concatenate cluster data.

        Args:
            device: Open device/image handle.
            start_cluster: First cluster of the chain.
            max_clusters: Safety cap on chain length.

        Returns:
            Concatenated raw bytes of every cluster in the chain. Stops (without
            error) at a free/bad cluster, a cycle, or the cap.
        """
        chunks: List[bytes] = []
        for cluster in self.walk_cluster_chain(device, start_cluster, max_clusters):
            chunks.append(read_exact(device, self.cluster_to_byte_offset(cluster), self.cluster_size))
        return b"".join(chunks)

    def walk_cluster_chain(
        self,
        device: BinaryIO,
        start_cluster: int,
        max_clusters: int = MAX_CHAIN_CLUSTERS,
    ) -> List[int]:
        """
        Resolve the full list of cluster numbers in a FAT chain.

        Args:
            device: Open device/image handle.
            start_cluster: First cluster of the chain.
            max_clusters: Safety cap on chain length.

        Returns:
            Cluster numbers in chain order. A cycle, free cluster mid-chain, or
            unreadable FAT entry ends the walk early (partial result) rather
            than raising — deleted-file recovery treats an incomplete chain as
            a signal to fall back to a contiguous-cluster assumption.
        """
        clusters: List[int] = []
        seen = set()
        cluster = start_cluster

        while (
            cluster >= 2
            and cluster not in seen
            and cluster < FAT_BAD_CLUSTER
            and len(clusters) < max_clusters
        ):
            seen.add(cluster)
            clusters.append(cluster)
            try:
                next_cluster = self.read_fat_entry(device, cluster)
            except OSError:
                break
            if next_cluster == FAT_FREE_CLUSTER or next_cluster >= FAT_MIN_EOC:
                break
            cluster = next_cluster

        return clusters
