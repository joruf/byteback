"""
Format-specific size detection and validation for carved file recovery.

Used when magic-byte signatures lack a reliable footer or when carved data
must be validated before presenting results to the user.
"""

import struct
from typing import Optional


def detect_file_size(data: bytes, signature_label: str) -> int:
    """
    Estimate the byte length of a carved file from its header data.

    Args:
        data: Bytes starting at the file header (may include partial content).
        signature_label: Human-readable signature label from config.

    Returns:
        Detected size in bytes, or 0 when the size cannot be determined.
    """
    parsers = {
        "JPEG Image": _jpeg_size,
        "PNG Image": _png_size,
        "GIF Image": _gif_size,
        "GIF Image (89a)": _gif_size,
        "PDF Document": _pdf_size,
        "ZIP Archive": _zip_size,
        "SQLite Database": _sqlite_size,
        "MP4 Video": _mp4_size,
        "ELF Executable": _elf_size,
    }
    parser = parsers.get(signature_label)
    if parser is None:
        return 0
    return parser(data)


def validate_carved_file(data: bytes, signature_label: str) -> bool:
    """
    Validate that carved bytes plausibly match the claimed format.

    Args:
        data: Full or partial carved file content.
        signature_label: Human-readable signature label from config.

    Returns:
        True when the data passes basic structural validation.
    """
    if not data:
        return False

    validators = {
        "JPEG Image": _validate_jpeg,
        "PNG Image": _validate_png,
        "GIF Image": _validate_gif,
        "GIF Image (89a)": _validate_gif,
        "PDF Document": _validate_pdf,
        "ZIP Archive": _validate_zip,
        "SQLite Database": _validate_sqlite,
        "MP4 Video": _validate_mp4,
        "ELF Executable": _validate_elf,
        "BMP Image": _validate_bmp,
    }
    validator = validators.get(signature_label)
    if validator is None:
        return len(data) >= 4
    return validator(data)


def _jpeg_size(data: bytes) -> int:
    """Walk JPEG markers to find EOI (0xFFD9)."""
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        return 0

    index = 2
    while index < len(data) - 1:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker == 0xD9:
            return index + 2
        if marker == 0xDA:
            end = data.find(b"\xff\xd9", index + 2)
            return end + 2 if end >= 0 else 0
        if index + 4 > len(data):
            return 0
        segment_length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        index += 2 + segment_length
    return 0


def _png_size(data: bytes) -> int:
    """Parse PNG chunks until IEND."""
    if len(data) < 8 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return 0

    index = 8
    while index + 12 <= len(data):
        chunk_length = struct.unpack(">I", data[index : index + 4])[0]
        chunk_type = data[index + 4 : index + 8]
        chunk_end = index + 12 + chunk_length
        if chunk_end > len(data):
            return 0
        if chunk_type == b"IEND":
            return chunk_end
        index = chunk_end
    return 0


def _gif_size(data: bytes) -> int:
    """GIF size from logical screen descriptor and blocks."""
    if len(data) < 10:
        return 0
    if not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
        return 0

    index = 10
    flags = data[10 - 1] if len(data) >= 10 else 0
    if flags & 0x80:
        palette_size = 3 * (2 ** ((flags & 0x07) + 1))
        index += palette_size

    while index < len(data):
        block = data[index]
        if block == 0x3B:
            return index + 1
        if block == 0x21:
            index += 2
            if index >= len(data):
                return 0
            while index < len(data) and data[index] != 0x00:
                index += 1 + data[index]
            index += 1
            continue
        if block == 0x2C:
            if index + 10 > len(data):
                return 0
            index += 10
            packed = data[index - 1]
            if packed & 0x80:
                index += 3 * (2 ** ((packed & 0x07) + 1))
            if index >= len(data):
                return 0
            index += 1
            while index < len(data):
                sub = data[index]
                index += 1
                if sub == 0:
                    break
                index += sub
            continue
        index += 1
    return 0


def _pdf_size(data: bytes) -> int:
    """Find last %%EOF marker in PDF data."""
    if not data.startswith(b"%PDF-"):
        return 0
    eof = data.rfind(b"%%EOF")
    if eof < 0:
        return 0
    end = eof + 5
    if end < len(data) and data[end : end + 1] in (b"\n", b"\r"):
        end += 1
    return end


def _zip_size(data: bytes) -> int:
    """Locate end-of-central-directory record."""
    if len(data) < 30 or not data.startswith(b"PK\x03\x04"):
        return 0
    eocd = data.rfind(b"PK\x05\x06")
    if eocd < 0:
        return 0
    if eocd + 22 > len(data):
        return 0
    comment_length = struct.unpack("<H", data[eocd + 20 : eocd + 22])[0]
    return eocd + 22 + comment_length


def _sqlite_size(data: bytes) -> int:
    """SQLite page size from header; estimate from available pages."""
    if len(data) < 100 or not data.startswith(b"SQLite format 3\x00"):
        return 0
    page_size = struct.unpack(">H", data[16:18])[0]
    if page_size == 1:
        page_size = 65536
    if page_size < 512:
        return 0
    pages = len(data) // page_size
    return pages * page_size


def _mp4_size(data: bytes) -> int:
    """Walk top-level MP4 boxes until the last complete box."""
    if len(data) < 8:
        return 0

    index = 0
    last_end = 0
    while index + 8 <= len(data):
        box_size = struct.unpack(">I", data[index : index + 4])[0]
        if box_size < 8:
            break
        if index + box_size > len(data):
            break
        last_end = index + box_size
        index += box_size
    return last_end


def _elf_size(data: bytes) -> int:
    """ELF header gives section/program header table locations."""
    if len(data) < 64 or not data.startswith(b"\x7fELF"):
        return 0
    if data[4] not in (1, 2):
        return 0

    if data[4] == 1:
        if len(data) < 52:
            return 0
        sh_offset = struct.unpack("<I", data[32:36])[0]
        sh_entry_size = struct.unpack("<H", data[46:48])[0]
        sh_count = struct.unpack("<H", data[48:50])[0]
        sh_str_index = struct.unpack("<H", data[50:52])[0]
    else:
        if len(data) < 64:
            return 0
        sh_offset = struct.unpack("<Q", data[40:48])[0]
        sh_entry_size = struct.unpack("<H", data[58:60])[0]
        sh_count = struct.unpack("<H", data[60:62])[0]
        sh_str_index = struct.unpack("<H", data[62:64])[0]

    if sh_count == 0 or sh_entry_size == 0:
        return len(data)

    table_end = sh_offset + (sh_count * sh_entry_size)
    if table_end <= len(data):
        return table_end
    return len(data)


def _validate_jpeg(data: bytes) -> bool:
    return data.startswith(b"\xff\xd8\xff") and (
        b"\xff\xd9" in data or len(data) >= 20
    )


def _validate_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n") and _png_size(data) > 0


def _validate_gif(data: bytes) -> bool:
    return (
        data.startswith(b"GIF87a") or data.startswith(b"GIF89a")
    ) and len(data) >= 13


def _validate_pdf(data: bytes) -> bool:
    return data.startswith(b"%PDF-") and b"%%EOF" in data


def _validate_zip(data: bytes) -> bool:
    return data.startswith(b"PK\x03\x04") and b"PK\x05\x06" in data


def _validate_sqlite(data: bytes) -> bool:
    return data.startswith(b"SQLite format 3\x00")


def _validate_mp4(data: bytes) -> bool:
    return len(data) >= 12 and (b"ftyp" in data[:32] or data[4:8] == b"ftyp")


def _validate_elf(data: bytes) -> bool:
    return data.startswith(b"\x7fELF") and data[4] in (1, 2)


def _validate_bmp(data: bytes) -> bool:
    if not data.startswith(b"BM") or len(data) < 26:
        return False
    file_size = struct.unpack("<I", data[2:6])[0]
    return 26 <= file_size <= len(data) + 1024
