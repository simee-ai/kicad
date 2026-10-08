"""PE (Windows) helpers: DLL imports, resolution against the shipped bin directory, version info."""

import re
from pathlib import Path

import pefile

# The Universal CRT is part of Windows 10 and later, which always loads its own copy and ignores one
# next to the program (and resolves api-ms-win-* API sets itself), so KiCad's app-local copy isn't shipped.
UCRT = re.compile(r"api-ms-win-.+\.dll|ucrtbase\.dll", re.I)
_DIRS = [pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
         pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]]


def deps(binary: Path) -> list[str]:
    pe = pefile.PE(str(binary), fast_load=True)
    try:
        # DLL names only: with symbols, pefile stops at its import-symbol limit and silently drops
        # the remaining DLLs (_eeschema.dll imports thousands of wx symbols first).
        pe.parse_data_directories(directories=_DIRS, import_dllnames_only=True)
        entries = getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
        return [e.dll.decode() for e in entries]
    finally:
        pe.close()


def make_resolver(search_dirs: list[Path]):
    """DLL names resolve case-insensitively to files KiCad ships; anything else is a Windows
    system DLL (KERNEL32, the UCRT, ...) and returns None. A DLL that's genuinely missing also
    returns None, which the Windows smoke test catches."""
    index = {p.name.lower(): p for d in search_dirs for p in d.iterdir() if p.is_file()}

    def resolve(name: str, _binary: Path) -> Path | None:
        return None if UCRT.fullmatch(name) else index.get(name.lower())

    return resolve


def version_info(binary: Path) -> dict[str, str]:
    """The strings of a binary's version resource (FileVersion, CompanyName, ...), if it has one."""
    pe = pefile.PE(str(binary), fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        return {k.decode(errors="replace"): v.decode(errors="replace")
                for info in getattr(pe, "FileInfo", None) or [] for entry in info
                for table in getattr(entry, "StringTable", None) or [] for k, v in table.entries.items()}
    finally:
        pe.close()
