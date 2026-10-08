"""Keep the download cache bounded: official installers live in <cache>/<version>/ (1 to 2 GB per
version); the Debian, macOS and Windows sources, and what each Homebrew bottle holds, in folders of their own
(mostly shared between releases)."""

import re
import shutil
import time
from pathlib import Path

DEBIAN_SOURCES = "debian-sources"
HOMEBREW_BOTTLES = "homebrew-bottles"  # what each bottle holds (UUIDs, formula, SBOM), not the bottle
MACOS_SOURCES = "macos-sources"
WINDOWS_SOURCES = "windows-sources"  # vcpkg ports' downloads and port folders
VCPKG_REGISTRIES = "vcpkg-registries"  # treeless clones, a few MB each; not pruned
SOURCES_MAX_AGE_DAYS = 90  # monthly builds, so a source no build used in three months is gone from KiCad's image

_VERSION = re.compile(r"\d+(\.\d+)*")


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def prune(cache: Path, built: str) -> None:
    """Keep the installers of the version just built and of the newest other version (the pinned one,
    while a new release is being packaged), and the source files a build used recently."""
    versions = sorted((d for d in cache.iterdir() if d.is_dir() and _VERSION.fullmatch(d.name)),
                      key=lambda d: _key(d.name)) if cache.is_dir() else []
    others = [d for d in versions if d.name != built]
    for stale in others[:-1]:
        shutil.rmtree(stale)
    for sources in (DEBIAN_SOURCES, HOMEBREW_BOTTLES, MACOS_SOURCES, WINDOWS_SOURCES):
        _prune_unused(cache / sources, time.time() - SOURCES_MAX_AGE_DAYS * 24 * 3600)


def _prune_unused(root: Path, cutoff: float) -> None:
    """Delete files under root last used (mtime) before cutoff, then the folders that leaves empty."""
    if not root.is_dir():
        return
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
    for folder in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
