"""
Magic-byte file signatures used by the carving engine.
"""

FILE_SIGNATURES = {
    "JPEG Image": {
        "extensions": [".jpg", ".jpeg"],
        "header": b"\xff\xd8\xff",
        "footer": b"\xff\xd9",
        "max_size": 50 * 1024 * 1024,
    },
    "PNG Image": {
        "extensions": [".png"],
        "header": b"\x89PNG\r\n\x1a\n",
        "footer": b"IEND\xaeB`\x82",
        "max_size": 30 * 1024 * 1024,
    },
    "GIF Image": {
        "extensions": [".gif"],
        "header": b"GIF87a",
        "footer": None,
        "max_size": 20 * 1024 * 1024,
    },
    "GIF Image (89a)": {
        "extensions": [".gif"],
        "header": b"GIF89a",
        "footer": None,
        "max_size": 20 * 1024 * 1024,
    },
    "BMP Image": {
        "extensions": [".bmp"],
        "header": b"BM",
        "footer": None,
        "max_size": 30 * 1024 * 1024,
    },
    "TIFF Image (LE)": {
        "extensions": [".tif", ".tiff"],
        "header": b"II*\x00",
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
    "TIFF Image (BE)": {
        "extensions": [".tif", ".tiff"],
        "header": b"MM\x00*",
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
    "WebP Image": {
        "extensions": [".webp"],
        "header": b"WEBP",
        "header_offset": 8,
        "footer": None,
        "max_size": 30 * 1024 * 1024,
    },
    "PDF Document": {
        "extensions": [".pdf"],
        "header": b"%PDF-",
        "footer": b"%%EOF",
        "max_size": 100 * 1024 * 1024,
    },
    "RTF Document": {
        "extensions": [".rtf"],
        "header": b"{\\rtf",
        "footer": None,
        "max_size": 20 * 1024 * 1024,
    },
    "ZIP Archive": {
        "extensions": [".zip"],
        "header": b"PK\x03\x04",
        "footer": None,
        "max_size": 200 * 1024 * 1024,
    },
    "7-Zip Archive": {
        "extensions": [".7z"],
        "header": b"7z\xbc\xaf\x27\x1c",
        "footer": None,
        "max_size": 200 * 1024 * 1024,
    },
    "RAR Archive": {
        "extensions": [".rar"],
        "header": b"Rar!\x1a\x07\x00",
        "footer": None,
        "max_size": 200 * 1024 * 1024,
    },
    "GZIP Archive": {
        "extensions": [".gz"],
        "header": b"\x1f\x8b\x08",
        "footer": None,
        "max_size": 100 * 1024 * 1024,
    },
    "Microsoft Office Document": {
        "extensions": [".doc", ".xls", ".ppt"],
        "header": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
    "SQLite Database": {
        "extensions": [".db", ".sqlite", ".sqlite3"],
        "header": b"SQLite format 3\x00",
        "footer": None,
        "max_size": 100 * 1024 * 1024,
    },
    "WAV Audio": {
        "extensions": [".wav"],
        "header": b"WAVE",
        "header_offset": 8,
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
    "MP3 Audio (ID3)": {
        "extensions": [".mp3"],
        "header": b"ID3",
        "footer": None,
        "max_size": 20 * 1024 * 1024,
    },
    "MP3 Audio (frame)": {
        "extensions": [".mp3"],
        "header": b"\xff\xfb",
        "footer": None,
        "max_size": 20 * 1024 * 1024,
    },
    "FLAC Audio": {
        "extensions": [".flac"],
        "header": b"fLaC",
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
    "OGG Audio": {
        "extensions": [".ogg", ".oga"],
        "header": b"OggS",
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
    "MP4 Video": {
        "extensions": [".mp4", ".m4v"],
        "header": b"ftyp",
        "header_offset": 4,
        "footer": None,
        "max_size": 500 * 1024 * 1024,
    },
    "AVI Video": {
        "extensions": [".avi"],
        "header": b"AVI ",
        "header_offset": 8,
        "footer": None,
        "max_size": 500 * 1024 * 1024,
    },
    "MKV Video": {
        "extensions": [".mkv"],
        "header": b"\x1a\x45\xdf\xa3",
        "footer": None,
        "max_size": 500 * 1024 * 1024,
    },
    "ELF Executable": {
        "extensions": [".elf"],
        "header": b"\x7fELF",
        "footer": None,
        "max_size": 50 * 1024 * 1024,
    },
}

CHECK_ON = "☑"
CHECK_OFF = "☐"
