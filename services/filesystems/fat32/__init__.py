"""
FAT32 filesystem support: boot sector parsing, directory/cluster-chain walking,
and deleted-file recovery.
"""

from services.filesystems.fat32.deleted_scanner import Fat32DeletedScanner

__all__ = ["Fat32DeletedScanner"]
