"""Official KiCad release assets (from the KiCad/kicad-source-mirror GitHub releases)."""

import fnmatch
import json
import shutil
import urllib.request
from pathlib import Path

from kicad_bundle.fetch import request, write_atomically

API = "https://api.github.com/repos/KiCad/kicad-source-mirror/releases"


def _release(version: str) -> dict:
    with urllib.request.urlopen(request(f"{API}/tags/{version}"), timeout=60) as resp:
        return json.load(resp)


def published_at(version: str) -> str:
    """When KiCad published the release (ISO 8601): its macOS build used what existed by then."""
    return _release(version)["published_at"]


def asset_url(version: str, pattern: str) -> str:
    assets = _release(version)["assets"]
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
        with urllib.request.urlopen(request(url), timeout=600) as resp:
            write_atomically(dest, lambda f: shutil.copyfileobj(resp, f, length=1 << 20))
    return dest

