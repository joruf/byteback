"""
FAT32 boot sector (BPB) parsing and cluster/FAT-chain arithmetic.
"""

from dataclasses import dataclass
from typing import BinaryIO, List

from services.filesystems.fat32.binary import (
    FAT32_BOOT_SIGNATURE,
    FAT32_BOOT_SIGNATURE_OFFSET,
    FAT32_FS_TYPE_LABEL,
    FAT32_FS_TYPE_OFFSET,
    FAT_BAD_CLUSTER,
    FAT_ENTRY_MASK,
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
class Fat32BootSector:
    """
    Parsed FAT32 boot sector fields required for recovery scanning.

    Attributes:
        bytes_per_sector: Sector size in bytes (almost always 512).
        sectors_per_cluster: Cluster size in sectors.
        reserved_sector_count: Sectors before the first FAT.
        num_fats: Number of FAT copies (almost always 2).
        fat_size_sectors: Size of one FAT, in sectors.
        root_cluster: Starting cluster of the root directory.
        total_sectors: Total sectors in the volume.
    """

    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sector_count: int
    num_fats: int
    fat_size_sectors: int
    root_cluster: int
    total_sectors: int

    @classmethod
    def read_from_device(cls, device: BinaryIO) -> "Fat32BootSector":
        """
        Parse the boot sector from a block device or image file.

        Args:
            device: Open binary handle positioned at the start of the volume.

        Returns:
            Parsed Fat32BootSector instance.

        Raises:
            ValueError: When the volume is not a valid FAT32 filesystem.
        """
        raw = read_exact(device, 0, 512)

        if read_le16(raw, FAT32_BOOT_SIGNATURE_OFFSET) != FAT32_BOOT_SIGNATURE:
            raise ValueError("Not a FAT volume (missing 0xAA55 boot signature)")

        bytes_per_sector = read_le16(raw, 0x0B)
        sectors_per_cluster = raw[0x0D]
        reserved_sector_count = read_le16(raw, 0x0E)
        num_fats = raw[0x10]
        root_entry_count = read_le16(raw, 0x11)
        fat_size_16 = read_le16(raw, 0x16)
        total_sectors_16 = read_le16(raw, 0x13)
        total_sectors_32 = read_le32(raw, 0x20)
        fat_size_32 = read_le32(raw, 0x24)
        root_cluster = read_le32(raw, 0x2C)
        fs_type = raw[FAT32_FS_TYPE_OFFSET : FAT32_FS_TYPE_OFFSET + 8]

        # FAT32 never uses a fixed root directory region (root_entry_count == 0) or
        # the legacy 16-bit FAT size field; the fs_type label is the usual tell but
        # not authoritative on every formatter, so both signals are checked.
        looks_like_fat32 = root_entry_count == 0 and fat_size_16 == 0 and fat_size_32 != 0
        if fs_type != FAT32_FS_TYPE_LABEL and not looks_like_fat32:
            raise ValueError("Not a FAT32 filesystem (FAT12/FAT16 or unrecognized)")
        if not looks_like_fat32:
            raise ValueError("Not a FAT32 filesystem (missing FAT32-style BPB fields)")

        if bytes_per_sector == 0 or sectors_per_cluster == 0 or num_fats == 0:
            raise ValueError("Invalid FAT32 BPB: zero-sized sector/cluster/FAT field")

        return cls(
            bytes_per_sector=bytes_per_sector,
            sectors_per_cluster=sectors_per_cluster,
            reserved_sector_count=reserved_sector_count,
            num_fats=num_fats,
            fat_size_sectors=fat_size_32,
            root_cluster=root_cluster,
            total_sectors=total_sectors_32 or total_sectors_16,
        )

    @property
    def cluster_size(self) -> int:
        """Cluster size in bytes."""
        return self.sectors_per_cluster * self.bytes_per_sector

    @property
    def fat_start_byte(self) -> int:
        """Absolute byte offset of the first FAT."""
        return self.reserved_sector_count * self.bytes_per_sector

    @property
    def data_start_sector(self) -> int:
        """First sector of the cluster data area (cluster 2)."""
        return self.reserved_sector_count + self.num_fats * self.fat_size_sectors

    def cluster_to_byte_offset(self, cluster: int) -> int:
        """
        Convert a cluster number to its absolute byte offset in the volume.

        Args:
            cluster: Cluster number (valid data clusters start at 2).

        Returns:
            Absolute byte offset of the start of that cluster.
        """
        sector = self.data_start_sector + (cluster - 2) * self.sectors_per_cluster
        return sector * self.bytes_per_sector

    def read_fat_entry(self, device: BinaryIO, cluster: int) -> int:
        """
        Read one 32-bit FAT entry (masked to its significant 28 bits).

        Args:
            device: Open device/image handle.
            cluster: Cluster number to look up.

        Returns:
            Masked FAT entry value (0 = free, >= FAT_MIN_EOC = end of chain).
        """
        offset = self.fat_start_byte + cluster * 4
        raw = read_exact(device, offset, 4)
        return read_le32(raw, 0) & FAT_ENTRY_MASK

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
            error) at a free/bad cluster, a cycle, or the cap — callers that need
            to distinguish "clean end of chain" from "broken/truncated chain"
            should use ``walk_cluster_chain`` directly.
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
