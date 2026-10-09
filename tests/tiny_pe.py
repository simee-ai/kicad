"""Smallest PE32+ image pefile reads: headers only, with the linker version MSVC stamps."""

import struct
from pathlib import Path


def make_pe(path: Path, linker: tuple[int, int] = (14, 44)) -> Path:
    dos = b"MZ" + b"\0" * 58 + struct.pack("<I", 64)
    coff = struct.pack("<HHIIIHH", 0x8664, 0, 0, 0, 0, 240, 0x22)
    optional = bytearray(240)
    struct.pack_into("<HBB", optional, 0, 0x20B, *linker)
    struct.pack_into("<II", optional, 32, 0x1000, 0x200)  # section, file alignment
    struct.pack_into("<I", optional, 60, 0x200)  # size of headers
    struct.pack_into("<I", optional, 108, 16)  # number of data directories
    data = dos + b"PE\0\0" + coff + bytes(optional)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data + b"\0" * (0x200 - len(data)))
    return path
