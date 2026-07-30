"""
NTFS MFT record parsing: fixup application, attribute walking, filename and
data extraction.
"""

from dataclasses import dataclass
from typing import BinaryIO, List, Optional

from services.filesystems.ntfs.binary import (
    ATTR_DATA,
    ATTR_END_MARKER,
    ATTR_FILE_NAME,
    FILE_NAME_TYPE_DOS_ONLY,
    MFT_RECORD_IN_USE,
    MFT_RECORD_IS_DIRECTORY,
    MFT_RECORD_MAGIC,
    read_le16,
    read_le32,
    read_le64,
)
from services.filesystems.ntfs.boot_sector import NtfsBootSector
from services.filesystems.ntfs.data_run import decode_data_runs, read_virtual_range, resolve_runs_to_clusters


@dataclass
class MftAttribute:
    """
    One parsed attribute header from an MFT record.

    Attributes:
        attribute_type: Attribute type code (e.g. 0x30 for $FILE_NAME).
        is_non_resident: True when content lives in cluster runs, not inline.
        resident_content: Inline content bytes (only set when resident).
        data_runs: Decoded (lcn, count) runs (only set when non-resident).
        real_size: Real content size in bytes (only set when non-resident).
    """

    attribute_type: int
    is_non_resident: bool
    resident_content: bytes
    data_runs: list
    real_size: int


class MftRecord:
    """
    One parsed MFT record (a file, directory, or system metadata entry).
    """

    def __init__(self, record_number: int, flags: int, attributes: List[MftAttribute]) -> None:
        """
        Args:
            record_number: Absolute MFT record number.
            flags: Raw MFT record flags (bit0 = in use, bit1 = directory).
            attributes: Parsed attribute headers, in on-disk order.
        """
        self.record_number = record_number
        self.flags = flags
        self.attributes = attributes

    @property
    def is_in_use(self) -> bool:
        """True when the record's InUse flag is set (not deleted)."""
        return bool(self.flags & MFT_RECORD_IN_USE)

    @property
    def is_deleted(self) -> bool:
        """True when the record's InUse flag is cleared."""
        return not self.is_in_use

    @property
    def is_directory(self) -> bool:
        """True when the record represents a directory."""
        return bool(self.flags & MFT_RECORD_IS_DIRECTORY)

    def get_attribute(self, attribute_type: int) -> Optional[MftAttribute]:
        """Return the first attribute of the given type, if present."""
        for attribute in self.attributes:
            if attribute.attribute_type == attribute_type:
                return attribute
        return None

    def get_file_name(self) -> Optional[str]:
        """
        Return the best available filename.

        A file can have more than one $FILE_NAME attribute (e.g. a long Win32
        name plus a short DOS 8.3 alias) — any namespace other than
        DOS-only is preferred, since that's normally the "real" name a user
        would recognize.
        """
        best_name: Optional[str] = None
        for attribute in self.attributes:
            if attribute.attribute_type != ATTR_FILE_NAME or attribute.is_non_resident:
                continue
            content = attribute.resident_content
            if len(content) < 0x42:
                continue
            name_length = content[0x40]
            name_type = content[0x41]
            name_bytes = content[0x42 : 0x42 + name_length * 2]
            try:
                name = name_bytes.decode("utf-16-le")
            except UnicodeDecodeError:
                continue
            if not name:
                continue
            if name_type != FILE_NAME_TYPE_DOS_ONLY:
                return name
            best_name = best_name or name
        return best_name

    def read_data(self, device: BinaryIO, boot_sector: NtfsBootSector) -> bytes:
        """
        Read the unnamed $DATA attribute's content (resident or non-resident).

        Args:
            device: Open device/image handle.
            boot_sector: Parsed NTFS boot sector.

        Returns:
            File content bytes, or an empty bytes object when there is no
            $DATA attribute.
        """
        data_attr = self.get_attribute(ATTR_DATA)
        if data_attr is None:
            return b""
        if not data_attr.is_non_resident:
            return data_attr.resident_content

        clusters = resolve_runs_to_clusters(data_attr.data_runs)
        return read_virtual_range(device, clusters, boot_sector.cluster_size, 0, data_attr.real_size)

    @classmethod
    def parse(cls, record_number: int, raw: bytes) -> Optional["MftRecord"]:
        """
        Apply the update-sequence fixup and parse one MFT record.

        Args:
            record_number: Absolute MFT record number (for diagnostics only).
            raw: Exactly one MFT record's raw bytes.

        Returns:
            Parsed MftRecord, or None when the record is not a valid "FILE"
            record (unused slot, corrupt, or a "BAAD" record).
        """
        if len(raw) < 0x30 or raw[0:4] != MFT_RECORD_MAGIC:
            return None

        record = bytearray(raw)
        usa_offset = read_le16(record, 0x04)
        usa_size = read_le16(record, 0x06)

        for i in range(1, usa_size):
            sector_end = i * 512
            if sector_end > len(record) or usa_offset + i * 2 + 2 > len(record):
                break
            record[sector_end - 2 : sector_end] = record[usa_offset + i * 2 : usa_offset + i * 2 + 2]

        flags = read_le16(record, 0x16)
        first_attr_offset = read_le16(record, 0x14)
        used_size = read_le32(record, 0x18)

        attributes = list(cls._parse_attributes(bytes(record), first_attr_offset, used_size))
        return cls(record_number=record_number, flags=flags, attributes=attributes)

    @staticmethod
    def _parse_attributes(record: bytes, start_offset: int, used_size: int):
        """Yield MftAttribute for each attribute header found in the record."""
        offset = start_offset
        limit = min(used_size, len(record)) if used_size else len(record)

        while offset + 8 <= limit:
            attribute_type = read_le32(record, offset)
            if attribute_type == ATTR_END_MARKER:
                break

            length = read_le32(record, offset + 4)
            if length < 8 or offset + length > len(record):
                break

            is_non_resident = bool(record[offset + 8])
            resident_content = b""
            data_runs: list = []
            real_size = 0

            if is_non_resident:
                if offset + 0x38 <= len(record):
                    real_size = read_le64(record, offset + 0x30)
                    runs_offset = read_le16(record, offset + 0x20)
                    if runs_offset and offset + runs_offset <= offset + length:
                        data_runs = decode_data_runs(record[offset + runs_offset : offset + length])
            else:
                if offset + 0x16 <= len(record):
                    content_len = read_le32(record, offset + 0x10)
                    content_offset = read_le16(record, offset + 0x14)
                    resident_content = record[offset + content_offset : offset + content_offset + content_len]

            yield MftAttribute(
                attribute_type=attribute_type,
                is_non_resident=is_non_resident,
                resident_content=resident_content,
                data_runs=data_runs,
                real_size=real_size,
            )

            offset += length
