"""Homebrew provenance of the libraries in KiCad's macOS DMG, which its builder takes from Homebrew
bottles (relinked and re-signed, which keeps each Mach-O UUID). A library's bottle is found by walking
its formula's homebrew-core history back from the KiCad release until a bottle for the macOS it was
built for holds a file with the same UUID; that bottle's SBOM names the exact source archive, and its
formula (in the bottle's .brew/) the patches Homebrew applied. A keg Homebrew built from source on
KiCad's build machine (no bottle for its macOS, as for Intel Macs since September 2026) takes them from
the same version's bottle for another tag (_from_source)."""

import hashlib
import io
import json
import re
import tarfile
import urllib.parse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from kicad_bundle import macho
from kicad_bundle.fetch import Fetch, cached_file, write_atomically
from kicad_bundle.macho import Slice

CORE = "Homebrew/homebrew-core"
COMMITS = f"https://api.github.com/repos/{CORE}/commits"
RAW = f"https://raw.githubusercontent.com/{CORE}"
GHCR = "https://ghcr.io/v2/homebrew/core"
# Distinct bottles to try per library before giving up. KiCad's build machine can lag Homebrew by months:
# 10.0.7-rc2 shipped glib 2.86.3, ten bottles back. Each try downloads the bottle once (then cached).
MAX_BOTTLES = 24

# Bundled file name -> the formula whose bottle ships it. A library none matches fails the build.
FORMULAE = [(re.compile(pattern), formula) for pattern, formula in (
    (r"libabsl_.+", "abseil"),
    (r"libboost_.+", "boost"),
    (r"libcairo\..+", "cairo"),
    (r"lib(?:ssl|crypto)\.(\d+)\..+", r"openssl@\1"),
    (r"libfontconfig\..+", "fontconfig"),
    (r"libfreetype\..+", "freetype"),
    (r"libgit2\..+", "libgit2"),
    (r"libglib-2\.0\..+", "glib"),
    (r"libgraphite2\..+", "graphite2"),
    (r"libharfbuzz\..+", "harfbuzz"),
    (r"libicu(?:data|i18n|uc)\.(\d+)\..+", r"icu4c@\1"),
    (r"libintl\..+", "gettext"),
    (r"libllhttp\..+", "llhttp"),
    (r"libltdl\..+", "libtool"),
    (r"libnng\..+", "nng"),
    (r"libodbc\..+", "unixodbc"),
    (r"libpcre2-8\..+", "pcre2"),
    (r"libpixman-1\..+", "pixman"),
    (r"libpng16\..+", "libpng"),
    (r"lib(?:protobuf|utf8_validity)\..+", "protobuf"),
    (r"libssh2\..+", "libssh2"),
    (r"libtbb(?:malloc)?\..+", "tbb"),
    (r"libTK\w+\..+", "opencascade"),
    (r"libX11\..+", "libx11"),
    (r"libXau\..+", "libxau"),
    (r"libxcb(?:-[\w-]+)?\..+", "libxcb"),
    (r"libXdmcp\..+", "libxdmcp"),
    (r"libXext\..+", "libxext"),
    (r"libXrender\..+", "libxrender"),
    (r"libzstd\..+", "zstd"),
)]
# macOS major version -> Homebrew's bottle tag name (prefixed arm64_ for Apple silicon).
MACOS = {11: "big_sur", 12: "monterey", 13: "ventura", 14: "sonoma", 15: "sequoia", 26: "tahoe", 27: "golden_gate"}


@dataclass(frozen=True)
class Bottle:
    formula: str
    version: str  # the keg's, e.g. 2.88.3 or 3.6.1_1 (a formula revision)
    tag: str
    sha256: str
    commit: str  # the homebrew-core commit that added this bottle (its formula built it)
    info: Path  # cached: uuids.json, the formula it was built from, its SBOM
    # Set when no bottle holds the libraries: Homebrew built this keg version from source for that tag
    # (see _from_source), and this bottle, the same keg version's for another tag, stands in for it.
    built_for: str | None = None


def formula(name: str) -> str | None:
    for pattern, template in FORMULAE:
        if m := pattern.fullmatch(name):
            return m.expand(template)
    return None


def formula_path(name: str) -> str:
    return f"Formula/{'lib' if name.startswith('lib') else name[0]}/{name}.rb"


def bottle_tag(arch: str, minos: tuple[int, int]) -> str:
    if minos[0] not in MACOS:
        raise RuntimeError(f"no Homebrew bottle tag known for macOS {minos[0]}: add it to homebrew.MACOS")
    return f"arm64_{MACOS[minos[0]]}" if arch == "arm64" else MACOS[minos[0]]


def bottle_shas(rb: str) -> dict[str, str]:
    block = re.search(r"^\s*bottle do\n(.*?)^\s*end\b", rb, re.M | re.S)
    return dict(re.findall(r'(\w+):\s*"([0-9a-f]{64})"', block.group(1))) if block else {}


def patches(rb: str) -> list[tuple[str, str | None]]:
    """(url, sha256) of each `patch do url ... end`, (path in homebrew-core, None) of each `patch do
    file ... end`. Inline patches (`patch :DATA`) are part of the formula itself."""
    found = []
    for body in re.findall(r"^\s*patch(?:\s+:p\d+)?\s+do\n(.*?)^\s*end\b", rb, re.M | re.S):
        if url := re.search(r'^\s*url\s+"([^"]+)"', body, re.M):
            found.append((url.group(1), re.search(r'sha256\s+"([0-9a-f]{64})"', body).group(1)))
        elif file := re.search(r'^\s*file\s+"([^"]+)"', body, re.M):
            found.append((file.group(1), None))
    return found


def formula_source(rb: str) -> tuple[str, str]:
    """(url, sha256) of the formula's source archive: its top-level url, or the one in its `stable do`
    block. For bottles built before Homebrew shipped an SBOM in each one."""
    stable = re.search(r"^  stable do\n(.*?)^  end\b", rb, re.M | re.S)
    body, indent = (stable.group(1), " " * 4) if stable else (rb, " " * 2)
    url = re.search(rf'^{indent}url\s+"([^"]+)"\s*$', body, re.M)
    digest = re.search(rf'^{indent}sha256\s+"([0-9a-f]{{64}})"', body, re.M)
    if not (url and digest):
        raise RuntimeError(f"formula has no source url with a sha256 (a git checkout?):\n{rb[:300]}")
    return url.group(1), digest.group(1)


def _history(name: str, until: str, fetch: Fetch):
    """(commit, formula text) of each homebrew-core commit touching the formula up to until, newest first."""
    query = urllib.parse.urlencode({"path": formula_path(name), "until": until, "per_page": 100}, safe="/@:")
    for commit in json.loads(fetch(f"{COMMITS}?{query}")):
        yield commit["sha"], fetch(f"{RAW}/{commit['sha']}/{formula_path(name)}").decode()


def _blob(name: str, sha256: str, fetch: Fetch) -> bytes:
    data = fetch(f"{GHCR}/{name.replace('@', '/')}/blobs/sha256:{sha256}")
    if hashlib.sha256(data).hexdigest() != sha256:
        raise RuntimeError(f"{name}: bottle doesn't match its sha256 {sha256}")
    return data


def archive(bottle: Bottle, cache: Path, fetch: Fetch) -> Path:
    """cache/<sha256>/bottle.tar.gz: the bottle itself, to pour (brew_prefix.py)."""
    dest = cache / bottle.sha256 / "bottle.tar.gz"
    if dest.exists():
        dest.touch()
    else:
        data = _blob(bottle.formula, bottle.sha256, fetch)
        write_atomically(dest, lambda f: f.write(data))
    return dest


def bottle_at(name: str, tag: str, until: str, cache: Path, fetch: Fetch) -> Bottle:
    """The newest bottle of formula name for tag (or for every platform: `all`) before until, for a
    formula KiCad only builds against (header-only, so no bundled file names its bottle)."""
    for commit, rb in _history(name, until, fetch):
        shas = bottle_shas(rb)
        if sha := shas.get(tag) or shas.get("all"):
            info = _bottle_info(name, sha, cache, fetch)
            return Bottle(name, (info / "version").read_text(), tag, sha, commit, info)
    raise RuntimeError(f"{name}: no {tag} bottle before {until}")


def _bottle_info(name: str, sha256: str, cache: Path, fetch: Fetch) -> Path:
    """cache/<sha256>/: uuids.json (UUID of each Mach-O file in the bottle -> its path in the keg),
    version, formula.rb (the formula it was built from) and sbom.spdx.json (if it has one). The bottle isn't kept."""
    info = cache / sha256
    if (info / "uuids.json").exists():
        for f in info.iterdir():
            f.touch()  # marks it used, so cache.prune keeps it
        return info
    data = _blob(name, sha256, fetch)
    uuids, extra = {}, {}
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar:
            parts = member.name.split("/")
            if not member.isfile() or len(parts) < 3:
                continue
            extra.setdefault("version", parts[1].encode())
            rel = "/".join(parts[2:])
            if rel == f".brew/{parts[0]}.rb":
                extra["formula.rb"] = tar.extractfile(member).read()
            elif rel == "sbom.spdx.json":
                extra["sbom.spdx.json"] = tar.extractfile(member).read()
            elif rel.startswith("lib/"):
                for s in macho.slices(tar.extractfile(member).read()).values():
                    uuids[s.uuid] = rel
    for file, content in extra.items():
        write_atomically(info / file, lambda f: f.write(content))
    write_atomically(info / "uuids.json", lambda f: f.write(json.dumps(uuids, indent=1).encode()))
    return info


def _walk(name: str, until: str, cache: Path, fetch: Fetch, tags: Callable[[str, dict[str, str]], list[str]],
          accept: Callable[[Path], bool]) -> tuple[Bottle | None, int]:
    """Walk the formula's history back from until, trying the bottles of tags(formula text, its bottle
    shas) at each commit, until accept(bottle info) holds; returns that bottle at the commit that added
    it (None if none did) and how many bottles were tried. Later commits may still name it (a version
    bump keeps the old bottle block until Homebrew's separate "update bottle" commit) while their
    formula and patch files already belong to the next version."""
    tried, found = [], None
    for commit, rb in _history(name, until, fetch):
        shas = bottle_shas(rb)
        if found:
            if shas.get(found.tag) != found.sha256:
                break
            found = replace(found, commit=commit)
            continue
        for tag in tags(rb, shas):
            if (sha := shas[tag]) in tried or len(tried) == MAX_BOTTLES:
                continue
            tried.append(sha)
            info = _bottle_info(name, sha, cache, fetch)
            if accept(info):
                found = Bottle(name, (info / "version").read_text(), tag, sha, commit, info)
                break
        if not found and len(tried) == MAX_BOTTLES:
            break
    return found, len(tried)


def _from_source(name: str, tag: str, libs: dict[str, Slice], until: str, cache: Path, fetch: Fetch,
                 tried: int) -> Bottle:
    """For libraries no bottle holds: Homebrew built their keg from source (no bottle for tag, e.g. on an
    Intel Mac since Homebrew stopped bottling for them in September 2026), which left the keg's path
    (Cellar/<formula>/<version>/) in them. The newest bottle of that keg version for another tag (the
    other architecture's first) stands in: same formula and source, and the same headers."""
    versions = {v for s in libs.values() for f, v in s.kegs if f == name}
    if len(versions) != 1:
        kegs = f"{len(versions)} {name} kegs {sorted(versions)}" if versions else f"no {name} keg"
        raise RuntimeError(f"{name}: no {tag} bottle before {until} holds {sorted(libs)} (tried {tried}), and they "
                           f"name {kegs} (Cellar/{name}/<version>/) to tell which one Homebrew built from source, "
                           f"so their source is unknown")
    version = versions.pop()
    twin = tag.removeprefix("arm64_") if tag.startswith("arm64_") else f"arm64_{tag}"

    def tags(rb: str, shas: dict[str, str]) -> list[str]:
        if version.split("_")[0] not in rb:  # another version's formula: its bottles aren't that version
            return []
        others = sorted(t for t in shas if t not in (tag, twin) and not t.endswith("_linux"))
        return [t for t in (tag, twin, *others) if t in shas]

    found, standins = _walk(name, until, cache, fetch, tags, lambda info: (info / "version").read_text() == version)
    if found:
        return replace(found, built_for=tag)
    raise RuntimeError(f"{name} {version} in {sorted(libs)} was built from source (no {tag} bottle holds them), "
                       f"but there is no bottle of that version before {until} for any macOS (tried {standins}) "
                       f"to take its formula and headers from")


def match(name: str, arch: str, libs: dict[str, Slice], until: str, cache: Path, fetch: Fetch) -> Bottle:
    """The bottle of formula name whose files have the UUIDs of libs (file name -> its arch slice), at
    the commit that added it; for libraries no bottle holds, the keg they were built from source as
    (_from_source)."""
    tags = {bottle_tag(arch, s.minos) for s in libs.values()}
    if len(tags) != 1:
        raise RuntimeError(f"{name}: {sorted(libs)} were built for different macOS versions: {sorted(tags)}")
    tag, uuids = tags.pop(), {s.uuid for s in libs.values()}
    found, tried = _walk(name, until, cache, fetch, lambda rb, shas: [tag] if tag in shas else [],
                         lambda info: uuids <= json.loads((info / "uuids.json").read_text()).keys())
    return found or _from_source(name, tag, libs, until, cache, fetch, tried)


def sources(bottle: Bottle, cache: Path, fetch: Fetch) -> list[tuple[str, Path]]:
    """(name in the sources archive, cached file): the source archive the bottle's SBOM names, each
    patch its formula applies, in order (patches/<nn>-<name>), and the formula itself (<formula>.rb).
    A bottle without an SBOM takes the source archive its formula names."""
    rb = (bottle.info / "formula.rb").read_text()
    if (sbom := bottle.info / "sbom.spdx.json").exists():
        archive = next(p for p in json.loads(sbom.read_text())["packages"]
                       if p["SPDXID"].startswith("SPDXRef-Archive-"))
        url = archive["downloadLocation"]
        digest = next(c["checksumValue"] for c in archive["checksums"] if c["algorithm"] == "SHA256")
    else:  # bottled before Homebrew put an SBOM in each bottle
        url, digest = formula_source(rb)
    files = [(_basename(url), cached_file(_basename(url), url, digest, cache, fetch))]
    for n, (where, digest) in enumerate(patches(rb), 1):
        name = f"patches/{n:02d}-{_basename(where)}"
        if digest:
            files.append((name, cached_file(_basename(where), where, digest, cache, fetch)))
        else:  # a patch file in homebrew-core, at the commit that added the bottle
            dest = cache / "homebrew-core" / bottle.commit / where
            if not dest.exists():
                data = fetch(f"{RAW}/{bottle.commit}/{where}")
                write_atomically(dest, lambda f: f.write(data))
            dest.touch()
            files.append((name, dest))
    return [*files, (f"{bottle.formula}.rb", bottle.info / "formula.rb")]


def _basename(url: str) -> str:
    return urllib.parse.urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
