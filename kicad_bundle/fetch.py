"""Downloads shared by the packagers: authenticated requests, retries, and a cache of files keyed by
their checksum, so sources a build already fetched are reused by the next one."""

import hashlib
import os
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import BinaryIO, Callable

Fetch = Callable[[str], bytes]


def request(url: str) -> urllib.request.Request:
    """A GET for url with the credentials its host takes: GITHUB_TOKEN for GitHub's API (60 requests
    an hour without one), the anonymous token for Homebrew's bottles on ghcr.io."""
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if url.startswith("https://api.github.com/") and (token := os.environ.get("GITHUB_TOKEN")):
        req.add_header("Authorization", f"Bearer {token}")
    elif url.startswith("https://ghcr.io/"):
        req.add_header("Authorization", "Bearer QQ==")
    return req


def fetch_url(url: str, tries: int = 4) -> bytes:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request(url), timeout=600) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == tries - 1 or getattr(e, "code", 500) in (403, 404):
                raise
            time.sleep(10 * (attempt + 1))  # snapshot.debian.org throttles bursts
    raise AssertionError("unreachable")


def write_atomically(dest: Path, write: Callable[[BinaryIO], None]) -> None:
    """write(f) into dest.part, then rename it to dest, so an interrupted download leaves no dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    with part.open("wb") as f:
        write(f)
    part.replace(dest)


def cached_file(name: str, url: str, digest: str, cache: Path, fetch: Fetch, algorithm: str = "sha256") -> Path:
    """cache/<digest[:2]>/<digest>/<name>, downloaded from url unless it's there already, and refused
    if its checksum isn't digest. Touched on reuse, so cache.prune keeps what builds still use."""
    dest = cache / digest[:2] / digest / name
    if not dest.exists():
        data = fetch(url)
        if hashlib.new(algorithm, data).hexdigest() != digest:
            raise RuntimeError(f"{name}: download from {url} doesn't match its {algorithm} {digest}")
        write_atomically(dest, lambda f: f.write(data))
    else:
        dest.touch()
    return dest


def add_dir(tar: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type, info.mode = tarfile.DIRTYPE, 0o755
    tar.addfile(info)
