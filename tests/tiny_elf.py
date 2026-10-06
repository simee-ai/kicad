"""Smallest x86_64 ELF shared object pyelftools reads: a .dynamic section with DT_NEEDED/DT_SONAME."""

import struct
from pathlib import Path

DT_NEEDED, DT_SONAME = 1, 14
SHDR = "<IIQQQQIIQQ"


def make_elf(path: Path, needed: tuple[str, ...] = (), soname: str | None = None) -> Path:
    strings = [*needed, *([soname] if soname else [])]
    dynstr, offsets = b"\0", {}
    for s in strings:
        offsets[s] = len(dynstr)
        dynstr += s.encode() + b"\0"
    dynstr += b"\0" * (-len(dynstr) % 8)
    tags = [(DT_NEEDED, offsets[n]) for n in needed] + ([(DT_SONAME, offsets[soname])] if soname else [])
    dynamic = b"".join(struct.pack("<qQ", t, v) for t, v in [*tags, (0, 0)])
    shstrtab = b"\0.dynstr\0.dynamic\0.shstrtab\0"
    shstrtab += b"\0" * (-len(shstrtab) % 8)

    off_dynstr = 64
    off_dynamic = off_dynstr + len(dynstr)
    off_shstrtab = off_dynamic + len(dynamic)
    off_sections = off_shstrtab + len(shstrtab)
    sections = [
        struct.pack(SHDR, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        struct.pack(SHDR, 1, 3, 0, 0, off_dynstr, len(dynstr), 0, 0, 1, 0),        # .dynstr
        struct.pack(SHDR, 9, 6, 0, 0, off_dynamic, len(dynamic), 1, 0, 8, 16),     # .dynamic
        struct.pack(SHDR, 18, 3, 0, 0, off_shstrtab, len(shstrtab), 0, 0, 1, 0),   # .shstrtab
    ]
    header = b"\x7fELF" + bytes([2, 1, 1, 0]) + bytes(8) + struct.pack(
        "<HHIQQQIHHHHHH", 3, 62, 1, 0, 0, off_sections, 0, 64, 0, 0, 64, len(sections), 3)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + dynstr + dynamic + shstrtab + b"".join(sections))
    return path
