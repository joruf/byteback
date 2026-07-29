"""
Format-specific size detection and validation for carved file recovery.

Used when magic-byte signatures lack a reliable footer or when carved data
must be validated before presenting results to the user.
"""

import re
import struct
import zlib
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
        "TIFF Image (LE)": _tiff_size,
        "TIFF Image (BE)": _tiff_size,
        "WebP Image": _riff_size,
        "WAV Audio": _riff_size,
        "AVI Video": _riff_size,
        "RTF Document": _rtf_size,
        "GZIP Archive": _gzip_size,
        "7-Zip Archive": _7z_size,
        "Microsoft Office Document": _cfbf_size,
        "MP3 Audio (ID3)": _mp3_size,
        "MP3 Audio (frame)": _mp3_size,
        "OGG Audio": _ogg_size,
        "MKV Video": _mkv_size,
        "TAR Archive": _tar_size,
        "PSD Image": _psd_size,
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
        "TIFF Image (LE)": _validate_tiff,
        "TIFF Image (BE)": _validate_tiff,
        "WebP Image": _validate_webp,
        "WAV Audio": _validate_wav,
        "AVI Video": _validate_avi,
        "RTF Document": _validate_rtf,
        "GZIP Archive": _validate_gzip,
        "7-Zip Archive": _validate_7z,
        "RAR Archive": _validate_rar,
        "Microsoft Office Document": _validate_cfbf,
        "MP3 Audio (ID3)": _validate_mp3,
        "MP3 Audio (frame)": _validate_mp3,
        "FLAC Audio": _validate_flac,
        "OGG Audio": _validate_ogg,
        "MKV Video": _validate_mkv,
        "TAR Archive": _validate_tar,
        "PSD Image": _validate_psd,
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
    """Walk top-level MP4/ISOBMFF boxes until the last complete box."""
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


_TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
_TIFF_TYPE_FORMATS = {1: "B", 3: "H", 4: "I"}
_TIFF_STRIP_OFFSETS_TAG = 273
_TIFF_STRIP_BYTE_COUNTS_TAG = 279
_TIFF_TILE_OFFSETS_TAG = 324
_TIFF_TILE_BYTE_COUNTS_TAG = 325


def _read_tiff_tag_values(data: bytes, endian: str, field_type: int, count: int, entry: bytes, elem_size: int):
    """Decode a TIFF tag's values (inline or out-of-line), or None if not decodable."""
    fmt = _TIFF_TYPE_FORMATS.get(field_type)
    if fmt is None:
        return None
    total_size = elem_size * count
    if total_size <= 4:
        raw = entry[8 : 8 + total_size]
    else:
        value_offset = struct.unpack(endian + "I", entry[8:12])[0]
        if value_offset + total_size > len(data):
            return None
        raw = data[value_offset : value_offset + total_size]
    try:
        return [
            struct.unpack(endian + fmt, raw[i * elem_size : (i + 1) * elem_size])[0] for i in range(count)
        ]
    except struct.error:
        return None


def _tiff_size(data: bytes) -> int:
    """
    Walk the TIFF IFD chain, bounding the size by the furthest tag value offset
    referenced anywhere in the tag tables, and additionally by the strip/tile
    pixel data those tag values point to (StripOffsets+StripByteCounts,
    TileOffsets+TileByteCounts).
    """
    if len(data) < 8:
        return 0
    if data[:4] == b"II*\x00":
        endian = "<"
    elif data[:4] == b"MM\x00*":
        endian = ">"
    else:
        return 0

    ifd_offset = struct.unpack(endian + "I", data[4:8])[0]
    max_end = 8
    visited = set()

    while ifd_offset and ifd_offset not in visited:
        if ifd_offset + 2 > len(data):
            return 0
        visited.add(ifd_offset)

        entry_count = struct.unpack(endian + "H", data[ifd_offset : ifd_offset + 2])[0]
        entries_start = ifd_offset + 2
        entries_end = entries_start + entry_count * 12
        if entries_end + 4 > len(data):
            return 0
        max_end = max(max_end, entries_end + 4)

        strip_offsets = strip_counts = tile_offsets = tile_counts = None

        for i in range(entry_count):
            entry = data[entries_start + i * 12 : entries_start + i * 12 + 12]
            tag = struct.unpack(endian + "H", entry[0:2])[0]
            field_type = struct.unpack(endian + "H", entry[2:4])[0]
            count = struct.unpack(endian + "I", entry[4:8])[0]
            elem_size = _TIFF_TYPE_SIZES.get(field_type, 1)
            total_size = elem_size * count
            if total_size > 4:
                value_offset = struct.unpack(endian + "I", entry[8:12])[0]
                max_end = max(max_end, value_offset + total_size)

            if tag in (_TIFF_STRIP_OFFSETS_TAG, _TIFF_TILE_OFFSETS_TAG):
                values = _read_tiff_tag_values(data, endian, field_type, count, entry, elem_size)
                if tag == _TIFF_STRIP_OFFSETS_TAG:
                    strip_offsets = values
                else:
                    tile_offsets = values
            elif tag in (_TIFF_STRIP_BYTE_COUNTS_TAG, _TIFF_TILE_BYTE_COUNTS_TAG):
                values = _read_tiff_tag_values(data, endian, field_type, count, entry, elem_size)
                if tag == _TIFF_STRIP_BYTE_COUNTS_TAG:
                    strip_counts = values
                else:
                    tile_counts = values

        for offsets, counts in ((strip_offsets, strip_counts), (tile_offsets, tile_counts)):
            if offsets and counts and len(offsets) == len(counts):
                for offset, byte_count in zip(offsets, counts):
                    max_end = max(max_end, offset + byte_count)

        ifd_offset = struct.unpack(endian + "I", data[entries_end : entries_end + 4])[0]

    return max_end


def _validate_tiff(data: bytes) -> bool:
    return (data[:4] in (b"II*\x00", b"MM\x00*")) and _tiff_size(data) > 8


def _riff_size(data: bytes) -> int:
    """RIFF container size (WebP, WAV, AVI): 8-byte header + declared chunk size."""
    if len(data) < 8 or not data.startswith(b"RIFF"):
        return 0
    riff_size = struct.unpack("<I", data[4:8])[0]
    total = 8 + riff_size
    return total + (total % 2)


def _validate_webp(data: bytes) -> bool:
    return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"


def _validate_wav(data: bytes) -> bool:
    return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE"


def _validate_avi(data: bytes) -> bool:
    return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"AVI "


_RTF_BIN_PATTERN = re.compile(rb"\\bin(-?\d+)")


def _rtf_size(data: bytes) -> int:
    """
    Find the end of the outermost RTF group by counting braces, skipping escaped
    braces (``\\{``, ``\\}``) and raw ``\\binN`` binary payloads that may contain
    arbitrary bytes (including brace characters) with no structural meaning.
    """
    if not data.startswith(b"{\\rtf"):
        return 0

    depth = 0
    index = 0
    length = len(data)
    while index < length:
        byte = data[index]
        if byte == 0x5C:  # backslash
            match = _RTF_BIN_PATTERN.match(data, index)
            if match:
                index = match.end()
                if index < length and data[index : index + 1] == b" ":
                    index += 1
                index += max(int(match.group(1)), 0)
                continue
            index += 2
            continue
        if byte == 0x7B:  # {
            depth += 1
            index += 1
            continue
        if byte == 0x7D:  # }
            depth -= 1
            index += 1
            if depth == 0:
                return index
            continue
        index += 1
    return 0


def _validate_rtf(data: bytes) -> bool:
    return data.startswith(b"{\\rtf")


def _gzip_size(data: bytes) -> int:
    """Decompress the gzip stream to find exactly where compressed data ends."""
    if len(data) < 18 or not data.startswith(b"\x1f\x8b\x08"):
        return 0
    try:
        decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
        decompressor.decompress(data)
    except zlib.error:
        return 0
    if not decompressor.eof:
        return 0
    return len(data) - len(decompressor.unused_data)


def _validate_gzip(data: bytes) -> bool:
    return data.startswith(b"\x1f\x8b\x08")


def _7z_size(data: bytes) -> int:
    """7z size = signature header (32 bytes) + next-header offset + next-header size."""
    if len(data) < 32 or not data.startswith(b"7z\xbc\xaf\x27\x1c"):
        return 0
    next_header_offset = struct.unpack("<Q", data[12:20])[0]
    next_header_size = struct.unpack("<Q", data[20:28])[0]
    return 32 + next_header_offset + next_header_size


def _validate_7z(data: bytes) -> bool:
    return len(data) >= 32 and data.startswith(b"7z\xbc\xaf\x27\x1c")


def _validate_rar(data: bytes) -> bool:
    return data.startswith(b"Rar!\x1a\x07\x00") or data.startswith(b"Rar!\x1a\x07\x01\x00")


def _cfbf_size(data: bytes) -> int:
    """
    Compound File Binary Format size (legacy .doc/.xls/.ppt): find the highest
    sector referenced by the FAT to determine total allocated sector count.

    Only handles the common case where all FAT sector locations fit in the
    109-entry header DIFAT (covers essentially all real-world legacy Office
    documents); returns 0 for the rare file needing additional DIFAT sectors.
    """
    if len(data) < 512 or not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return 0

    sector_shift = struct.unpack("<H", data[0x1E:0x20])[0]
    sector_size = 1 << sector_shift
    if sector_size < 512 or sector_size > (1 << 16):
        return 0

    num_fat_sectors = struct.unpack("<I", data[0x2C:0x30])[0]
    if num_fat_sectors == 0 or num_fat_sectors > 109:
        return 0

    entries_per_sector = sector_size // 4
    difat = data[0x4C : 0x4C + 109 * 4]

    highest_used_sector = -1
    for fat_index in range(num_fat_sectors):
        disk_sector = struct.unpack("<i", difat[fat_index * 4 : fat_index * 4 + 4])[0]
        if disk_sector < 0:
            return 0
        offset = 512 + disk_sector * sector_size
        if offset + sector_size > len(data):
            return 0
        fat_sector = data[offset : offset + sector_size]
        for local_index in range(entries_per_sector):
            value = struct.unpack("<I", fat_sector[local_index * 4 : local_index * 4 + 4])[0]
            if value != 0xFFFFFFFF:
                global_index = fat_index * entries_per_sector + local_index
                highest_used_sector = max(highest_used_sector, global_index)

    if highest_used_sector < 0:
        return 512
    return 512 + (highest_used_sector + 1) * sector_size


def _validate_cfbf(data: bytes) -> bool:
    return data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")


# MPEG audio bitrate tables in kbps, indexed by 4-bit bitrate field (index 0 = "free", 15 = invalid).
_MP3_BITRATES = {
    (1, 1): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448, None],
    (1, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384, None],
    (1, 3): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None],
    (2, 1): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256, None],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
    (2, 3): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None],
}
_MP3_SAMPLE_RATES = {
    1: [44100, 48000, 32000, None],
    2: [22050, 24000, 16000, None],
    3: [11025, 12000, 8000, None],  # "version 2.5", keyed as 3 to stay an int
}
_MP3_VERSION_MAP = {0b00: 3, 0b10: 2, 0b11: 1}  # 0b01 is reserved/invalid
_MP3_LAYER_MAP = {0b01: 3, 0b10: 2, 0b11: 1}  # 0b00 is reserved/invalid


def _parse_mp3_frame_header(data: bytes, offset: int) -> Optional[int]:
    """Parse one MPEG audio frame header at ``offset``; return its byte length or None."""
    if offset + 4 > len(data):
        return None
    if data[offset] != 0xFF or (data[offset + 1] & 0xE0) != 0xE0:
        return None

    version_bits = (data[offset + 1] >> 3) & 0x03
    layer_bits = (data[offset + 1] >> 1) & 0x03
    version = _MP3_VERSION_MAP.get(version_bits)
    layer = _MP3_LAYER_MAP.get(layer_bits)
    if version is None or layer is None:
        return None

    bitrate_index = (data[offset + 2] >> 4) & 0x0F
    sample_rate_index = (data[offset + 2] >> 2) & 0x03
    padding = (data[offset + 2] >> 1) & 0x01

    bitrate_table_version = 1 if version == 1 else 2
    bitrate_kbps = _MP3_BITRATES.get((bitrate_table_version, layer), [None] * 16)[bitrate_index]
    sample_rate = _MP3_SAMPLE_RATES.get(version, [None] * 4)[sample_rate_index]
    if not bitrate_kbps or not sample_rate:
        return None

    bitrate_bps = bitrate_kbps * 1000
    if layer == 1:
        frame_length = (12 * bitrate_bps // sample_rate + padding) * 4
    elif layer == 3 and version != 1:
        frame_length = 72 * bitrate_bps // sample_rate + padding
    else:
        frame_length = 144 * bitrate_bps // sample_rate + padding

    return frame_length if frame_length > 0 else None


def _mp3_size(data: bytes) -> int:
    """
    Walk consecutive MPEG audio frames (past any leading ID3v2 tag) until an
    invalid frame is found, returning the offset of the last valid frame's end.
    """
    offset = 0
    if data.startswith(b"ID3") and len(data) >= 10:
        tag_size = (
            (data[6] & 0x7F) << 21 | (data[7] & 0x7F) << 14 | (data[8] & 0x7F) << 7 | (data[9] & 0x7F)
        )
        offset = 10 + tag_size

    if offset >= len(data):
        return 0

    frame_count = 0
    last_end = offset
    while True:
        frame_length = _parse_mp3_frame_header(data, offset)
        if frame_length is None or offset + frame_length > len(data):
            break
        offset += frame_length
        last_end = offset
        frame_count += 1

    return last_end if frame_count > 0 else 0


def _validate_mp3(data: bytes) -> bool:
    if data.startswith(b"ID3"):
        return len(data) >= 10
    return _parse_mp3_frame_header(data, 0) is not None


def _validate_flac(data: bytes) -> bool:
    """FLAC: magic followed by a structurally sane STREAMINFO metadata block header."""
    if len(data) < 8 or not data.startswith(b"fLaC"):
        return False
    block_type = data[4] & 0x7F
    block_length = struct.unpack(">I", b"\x00" + data[5:8])[0]
    return block_type == 0 and block_length > 0  # type 0 == STREAMINFO, always first


def _ogg_size(data: bytes) -> int:
    """Walk Ogg pages until the end-of-stream page (header flag bit 0x04)."""
    if len(data) < 27 or not data.startswith(b"OggS"):
        return 0

    offset = 0
    while offset + 27 <= len(data):
        if data[offset : offset + 4] != b"OggS":
            return 0
        header_type = data[offset + 5]
        page_segments = data[offset + 26]
        header_len = 27 + page_segments
        if offset + header_len > len(data):
            return 0
        segment_table = data[offset + 27 : offset + header_len]
        payload_len = sum(segment_table)
        page_end = offset + header_len + payload_len
        if page_end > len(data):
            return 0
        if header_type & 0x04:
            return page_end
        offset = page_end
    return 0


def _validate_ogg(data: bytes) -> bool:
    return len(data) >= 27 and data.startswith(b"OggS")


def _read_ebml_vint(data: bytes, offset: int, keep_marker: bool):
    """
    Read one EBML variable-length integer starting at ``offset``.

    Returns:
        ``(value, offset_after, byte_length)``, or ``(None, offset, 0)`` when no
        valid vint starts at ``offset``.
    """
    if offset >= len(data):
        return None, offset, 0
    first = data[offset]
    if first == 0:
        return None, offset, 0

    length = 1
    mask = 0x80
    while mask and not (first & mask):
        mask >>= 1
        length += 1
    if mask == 0 or offset + length > len(data):
        return None, offset, 0

    value = first if keep_marker else (first & (mask - 1))
    for i in range(1, length):
        value = (value << 8) | data[offset + i]
    return value, offset + length, length


def _mkv_size(data: bytes) -> int:
    """
    Matroska/WebM (EBML) size: the top-level Segment element usually declares its
    own content size, giving an exact file length without decoding any content.
    Returns 0 for unbounded/streamed segments (no declared size).
    """
    if len(data) < 4 or not data.startswith(b"\x1a\x45\xdf\xa3"):
        return 0

    header_size_result = _read_ebml_vint(data, 4, keep_marker=False)
    if header_size_result[0] is None:
        return 0
    header_content_size, header_value_end, _ = header_size_result
    segment_start = header_value_end + header_content_size
    if segment_start + 4 > len(data):
        return 0

    segment_id = data[segment_start : segment_start + 4]
    if segment_id != b"\x18\x53\x80\x67":
        return 0

    size_result = _read_ebml_vint(data, segment_start + 4, keep_marker=False)
    if size_result[0] is None:
        return 0
    segment_size, size_value_end, size_length = size_result

    unknown_marker = (1 << (7 * size_length)) - 1
    if segment_size == unknown_marker:
        return 0

    return size_value_end + segment_size


def _validate_mkv(data: bytes) -> bool:
    return data.startswith(b"\x1a\x45\xdf\xa3")


def _tar_size(data: bytes) -> int:
    """Walk consecutive 512-byte POSIX tar headers/content until the terminator or EOF."""
    if len(data) < 512:
        return 0

    offset = 0
    zero_blocks = 0
    while offset + 512 <= len(data):
        block = data[offset : offset + 512]
        if block == b"\x00" * 512:
            zero_blocks += 1
            offset += 512
            if zero_blocks >= 2:
                return offset
            continue

        zero_blocks = 0
        if block[257:263] not in (b"ustar\x00", b"ustar "):
            return offset if offset > 0 else 0

        size_field = block[124:136].rstrip(b"\x00 ")
        try:
            entry_size = int(size_field, 8) if size_field else 0
        except ValueError:
            return offset if offset > 0 else 0

        entry_blocks = 1 + -(-entry_size // 512)
        offset += entry_blocks * 512

    return offset


def _validate_tar(data: bytes) -> bool:
    return len(data) >= 263 and data[257:263] in (b"ustar\x00", b"ustar ")


def _psd_size(data: bytes) -> int:
    """
    Photoshop (PSD/PSB) size: skip the three length-prefixed header sections
    precisely, then compute the final image-data section's length when it is
    stored uncompressed (compression == 0). Compressed final sections have no
    length field in the format itself, so those return 0 (fallback to max_size).
    """
    if len(data) < 26 or not data.startswith(b"8BPS"):
        return 0

    version = struct.unpack(">H", data[4:6])[0]
    if version not in (1, 2):
        return 0

    channels = struct.unpack(">H", data[12:14])[0]
    height = struct.unpack(">I", data[14:18])[0]
    width = struct.unpack(">I", data[18:22])[0]
    depth = struct.unpack(">H", data[22:24])[0]

    offset = 26
    for _ in range(2):  # Color Mode Data section, Image Resources section
        if offset + 4 > len(data):
            return 0
        section_length = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4 + section_length

    if offset + 4 > len(data):
        return 0
    length_field_size = 8 if version == 2 else 4
    if offset + length_field_size > len(data):
        return 0
    if version == 2:
        layer_section_length = struct.unpack(">Q", data[offset : offset + 8])[0]
    else:
        layer_section_length = struct.unpack(">I", data[offset : offset + 4])[0]
    offset += length_field_size + layer_section_length

    if offset + 2 > len(data):
        return 0
    compression = struct.unpack(">H", data[offset : offset + 2])[0]
    offset += 2

    if compression != 0:
        return 0  # RLE/ZIP-compressed image data has no length field; can't determine size

    bytes_per_row = -(-(width * depth) // 8)  # ceil(width * depth / 8)
    image_data_size = channels * height * bytes_per_row
    return offset + image_data_size


def _validate_psd(data: bytes) -> bool:
    return len(data) >= 26 and data.startswith(b"8BPS") and struct.unpack(">H", data[4:6])[0] in (1, 2)
