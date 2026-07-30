"""
NTFS filesystem support: boot sector parsing, MFT record parsing, and
deleted-file recovery.
"""

from services.filesystems.ntfs.deleted_scanner import NtfsDeletedScanner

__all__ = ["NtfsDeletedScanner"]
