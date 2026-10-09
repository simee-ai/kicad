"""Official KiCad installers: an asset of the KiCad/kicad-source-mirror GitHub release, or, for a version
that has none (a release candidate), the same installer on KiCad's download server."""

import fnmatch
import json
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

from kicad_bundle.fetch import Fetch, fetch_url, request, write_atomically

API = "https://api.github.com/repos/KiCad/kicad-source-mirror/releases"
DOWNLOADS = "https://kicad-downloads.s3.cern.ch"


@dataclass(frozen=True)
class Installer:
    url: str
    # When KiCad published it (ISO 8601): its builds used what existed by then. For a GitHub release its
    # publication; on the download server the installer's upload, after its build (the tag comes before).
    published_at: str


def _release(version: str, fetch: Fetch) -> dict | None:
    try:
        return json.loads(fetch(f"{API}/tags/{version}"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _download(version: str, path: str, fetch: Fetch) -> Installer:
    listing = fetch(f"{DOWNLOADS}/?prefix={urllib.parse.quote(path)}").decode()
    for body in re.findall(r"<Contents>(.*?)</Contents>", listing, re.S):
        if re.search(r"<Key>(.*?)</Key>", body).group(1) == path:
            modified = re.search(r"<LastModified>(.*?)</LastModified>", body).group(1)
            return Installer(f"{DOWNLOADS}/{path}", re.sub(r"\.\d+Z$", "Z", modified))
    raise RuntimeError(f"KiCad {version} has no GitHub release, and {DOWNLOADS}/{path} doesn't exist")


def installer(version: str, pattern: str, download: str, fetch: Fetch = fetch_url) -> Installer:
    """The release asset matching pattern, or, if KiCad has no GitHub release of version, download (its
    path on the download server, e.g. osx/stable/kicad-unified-universal-10.0.0-rc1.dmg)."""
    release = _release(version, fetch)
    if release is None:
        return _download(version, download, fetch)
    assets = release["assets"]
    matches = [a["browser_download_url"] for a in assets if fnmatch.fnmatch(a["name"], pattern)]
    if len(matches) != 1:
        raise RuntimeError(f"KiCad {version}: expected one asset matching {pattern!r}, found "
                           f"{[a['name'] for a in assets]}")
    return Installer(matches[0], release["published_at"])


def _stream(url: str) -> BinaryIO:
    return urllib.request.urlopen(request(url), timeout=600)


def cached(found: Installer, version: str, cache: Path, stream: Callable[[str], BinaryIO] = _stream) -> Path:
    """Download the installer into cache/<version>/ unless it's already there."""
    dest = cache / version / found.url.rsplit("/", 1)[1]
    if not (dest.exists() and dest.stat().st_size > 0):
        with stream(found.url) as resp:
            write_atomically(dest, lambda f: shutil.copyfileobj(resp, f, length=1 << 20))
    return dest
