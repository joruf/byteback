"""
exFAT filesystem support: boot sector parsing, directory-entry-set walking,
and deleted-file recovery.
"""

from services.filesystems.exfat.deleted_scanner import ExfatDeletedScanner

__all__ = ["ExfatDeletedScanner"]
