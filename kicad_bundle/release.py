"""Official KiCad release assets (from the KiCad/kicad-source-mirror GitHub releases)."""

import fnmatch
import json
import os
import shutil
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable

API = "https://api.github.com/repos/KiCad/kicad-source-mirror/releases"


def _get(url: str) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token := os.environ.get("GITHUB_TOKEN"):
        req.add_header("Authorization", f"Bearer {token}")
    return req


def asset_url(version: str, pattern: str) -> str:
    with urllib.request.urlopen(_get(f"{API}/tags/{version}"), timeout=60) as resp:
        assets = json.load(resp)["assets"]
    matches = [a["browser_download_url"] for a in assets if fnmatch.fnmatch(a["name"], pattern)]
    if len(matches) != 1:
        raise RuntimeError(f"KiCad {version}: expected one asset matching {pattern!r}, found "
                           f"{[a['name'] for a in assets]}")
    return matches[0]


def cached_asset(version: str, pattern: str, cache: Path) -> Path:
    """Download the release asset matching pattern into cache/<version>/ unless it's already there."""
    url = asset_url(version, pattern)
    dest = cache / version / url.rsplit("/", 1)[1]
    if not (dest.exists() and dest.stat().st_size > 0):
        with urllib.request.urlopen(_get(url), timeout=600) as resp:
            write_atomically(dest, lambda f: shutil.copyfileobj(resp, f, length=1 << 20))
    return dest


def write_atomically(dest: Path, write: Callable[[BinaryIO], None]) -> None:
    """write(f) into dest.part, then rename it to dest, so an interrupted download leaves no dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with part.open("wb") as f:
        write(f)
    part.replace(dest)
