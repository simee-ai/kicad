"""Debian provenance of files in an extracted image: the packages that own them and the exact source
of those packages (from snapshot.debian.org), so the Linux bundle can ship licences and sources."""

import json
import re
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
SOURCE_FILES = json.loads(Path(__file__).with_name("debian-sources.json").read_text(encoding="utf-8"))["packages"]


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    source: str
    source_version: str
    built_using: tuple[tuple[str, str], ...] = ()  # other sources it was built from: (source, version)


def _fields(stanza: str) -> dict[str, str]:
    return dict(line.split(": ", 1) for line in stanza.splitlines() if line and not line[0].isspace() and ": " in line)


def packages(status: str) -> dict[str, Package]:
    """Installed packages in a dpkg status file. Source defaults to the package itself, and its version
    to the package's (a binNMU names it: "Source: gmp (2:6.3.0+dfsg-3)"). Built-Using names the sources of
    what it was built with and contains, e.g. gcc-mingw-w64's libstdc++ is gcc-12's."""
    found = {}
    for stanza in status.split("\n\n"):
        f = _fields(stanza)
        if f.get("Status", "").split()[-1:] != ["installed"]:
            continue
        source, _, source_version = f.get("Source", f["Package"]).partition(" ")
        built_using = tuple(re.findall(r"([^\s,]+) \(= ([^)]+)\)", f.get("Built-Using", "")))
        found[f["Package"]] = Package(f["Package"], f["Version"], source, source_version.strip("()") or f["Version"],
                                      built_using)
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
    return f"{SNAPSHOT}/mr/package/{quoted}/srcfiles?fileinfo=0"


def source_files(reply: dict) -> list[tuple[str, str]]:
    """(file name, sha1) of every file of a source package, from snapshot's srcfiles reply."""
    return sorted((reply["fileinfo"][r["hash"]][0]["name"], r["hash"]) for r in reply["result"])


def descriptor(text: str) -> tuple[str, str, dict[str, str]]:
    """Source, Version and the files a source package's .dsc requires (name -> sha1)."""
    source, version = (re.search(rf"^{field}: (.+)$", text, re.M) for field in ("Source", "Version"))
    block = re.search(r"^Checksums-Sha1:\n((?:[ \t].+\n)+)", text, re.M)
    required = {line.split()[2]: line.split()[0] for line in block[1].splitlines()} if block else {}
    return source and source[1], version and version[1], required


def descriptor_files(files: dict[str, str], required: dict[str, str]) -> dict[str, str] | None:
    """files (name -> sha1) under the names their .dsc requires, or None if any required file is missing.
    Snapshot can give identical bytes an older name (glib's Unicode data, #54); files the .dsc doesn't
    list, such as .git.tar.xz, are kept."""
    if not required or not set(required.values()) <= set(files.values()):
        return None
    return {**{n: h for n, h in files.items() if h not in required.values()}, **required}


def _download(name: str, sha1: str, cache: Path, fetch: Fetch) -> Path:
    return cached_file(name, f"{SNAPSHOT}/file/{sha1}", sha1, cache, fetch, "sha1")


def _api_source_files(source: str, version: str, cache: Path, fetch: Fetch) -> dict[str, str]:
    reply = json.loads(fetch(srcfiles_url(source, version)))
    # The expanded query can time out while these smaller queries work (#53).
    reply["fileinfo"] = {
        r["hash"]: json.loads(fetch(f"{SNAPSHOT}/mr/file/{r['hash']}/info"))["result"]
        for r in reply["result"]
    }
    files = dict(source_files(reply))
    if not files:
        raise RuntimeError(f"{source} {version}: Snapshot returned no source files")
    dscs = [name for name in files if name.endswith(".dsc")]
    if len(dscs) != 1:
        raise RuntimeError(f"{source} {version}: Snapshot listed {len(dscs)} source descriptors, not one")
    dsc_source, dsc_version, required = descriptor(_download(dscs[0], files[dscs[0]], cache, fetch).read_text("utf-8"))
    named = descriptor_files(files, required)
    if (dsc_source, dsc_version) != (source, version) or named is None:
        raise RuntimeError(f"{source} {version}: Snapshot's files don't match the source descriptor {dscs[0]}")
    return named


def _source_package(source: str, version: str, cache: Path, fetch: Fetch) -> list[Path]:
    pinned = SOURCE_FILES.get(source, {}).get(version)
    files = _api_source_files(source, version, cache, fetch) if pinned is None else pinned
    return [_download(name, sha1, cache, fetch) for name, sha1 in sorted(files.items())]


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
