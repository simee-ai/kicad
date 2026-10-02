"""PE (Windows) helpers: DLL imports and resolution against the shipped bin directory."""

from pathlib import Path

import pefile

_DIRS = [pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
         pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]]


def deps(binary: Path) -> list[str]:
    pe = pefile.PE(str(binary), fast_load=True)
    try:
        pe.parse_data_directories(directories=_DIRS)
        entries = getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])
        return [e.dll.decode() for e in entries]
    finally:
        pe.close()


def make_resolver(search_dirs: list[Path]):
    """DLL names resolve case-insensitively to files KiCad ships; anything else is a Windows
    system DLL (KERNEL32, api-ms-win-*, ...) and returns None. A DLL that's genuinely missing also
    returns None, which the Windows smoke test catches."""
    index = {p.name.lower(): p for d in search_dirs for p in d.iterdir() if p.is_file()}

    def resolve(name: str, _binary: Path) -> Path | None:
        return index.get(name.lower())

    return resolve
