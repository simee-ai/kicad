"""Debian provenance of files in an extracted image: the packages that own them and the exact source
of those packages (from snapshot.debian.org), so the Linux bundle can ship licences and sources."""

import json
import tarfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from kicad_bundle.fetch import Fetch, add_dir, cached_file, fetch_url

DPKG_STATUS = "var/lib/dpkg/status"
DPKG_INFO = "var/lib/dpkg/info"
SNAPSHOT = "https://snapshot.debian.org"


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    source: str
    source_version: str


def _fields(stanza: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in stanza.splitlines() if line and not line[0].isspace() and ": " in line)


def packages(status: str) -> dict[str, Package]:
    """Installed packages in a dpkg status file. Source defaults to the package itself, and its version
    to the package's (a binNMU names it: "Source: gmp (2:6.3.0+dfsg-3)")."""
    found = {}
    for stanza in status.split("\n\n"):
        f = _fields(stanza)
        if f.get("Status", "").split()[-1:] != ["installed"]:
            continue
        source, _, source_version = f.get("Source", f["Package"]).partition(" ")
        found[f["Package"]] = Package(f["Package"], f["Version"], source, source_version.strip("()") or f["Version"])
    return found


def _owners(rootfs: Path) -> dict[str, str]:
    """Image path -> owning package, from dpkg's file lists (named <package>[:<arch>].list)."""
    owners = {}
    for listing in (rootfs / DPKG_INFO).glob("*.list"):
        name = listing.stem.split(":")[0]
        for path in listing.read_text().splitlines():
            owners[path] = name
    return owners


def provenance(rootfs: Path, files: Iterable[Path]) -> tuple[dict[Path, Package], list[Path]]:
    """The package owning each file (real files under rootfs), and the files no package owns."""
    owners, pkgs = _owners(rootfs), packages((rootfs / DPKG_STATUS).read_text())
    owned, unowned = {}, []
    for f in files:
        path = "/" + str(f.relative_to(rootfs))
        # Merged /usr: older packages still list /lib/... for what now lives in /usr/lib/...
        name = owners.get(path) or owners.get(path.removeprefix("/usr"))
        if name:
            owned[f] = pkgs[name]
        else:
            unowned.append(f)
    return owned, unowned


def srcfiles_url(source: str, version: str) -> str:
    quoted = "/".join(urllib.parse.quote(p, safe="") for p in (source, version))
    return f"{SNAPSHOT}/mr/package/{quoted}/srcfiles?fileinfo=1"


def source_files(reply: dict) -> list[tuple[str, str]]:
    """(file name, sha1) of every file of a source package, from snapshot's srcfiles reply."""
    return sorted((reply["fileinfo"][r["hash"]][0]["name"], r["hash"]) for r in reply["result"])


def _source_package(source: str, version: str, cache: Path, fetch: Fetch) -> list[Path]:
    reply = json.loads(fetch(srcfiles_url(source, version)))
    return [cached_file(name, f"{SNAPSHOT}/file/{sha1}", sha1, cache, fetch, "sha1") for name, sha1 in source_files(reply)]


def sources_archive(sources: Iterable[tuple[str, str]], dest: Path, cache: Path,
                    fetch: Fetch = fetch_url) -> Path:
    """dest (a .tar) holding <dest stem>/<source>_<version>/<every file of that Debian source package>,
    unpackable with `dpkg-source -x <the .dsc>`. Files are cached by sha1 under cache."""
    wanted = sorted(set(sources))
    with ThreadPoolExecutor(4) as pool:
        files = list(pool.map(lambda sv: _source_package(*sv, cache, fetch), wanted))
    top = dest.name.removesuffix(".tar")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w") as tar:  # the files are compressed already
        add_dir(tar, top)
        for (source, version), paths in zip(wanted, files):
            folder = f"{top}/{source}_{version.split(':', 1)[-1]}"  # Debian file names drop the epoch
            add_dir(tar, folder)
            for path in paths:
                tar.add(path, arcname=f"{folder}/{path.name}")
    return dest
