"""Licences and sources of the third-party libraries in the macOS bundle. Each library comes either
from a Homebrew bottle (found by Mach-O UUID, see homebrew.py) or from what kicad-mac-builder builds
itself (see macbuilder.py); a library that is neither, nor KiCad's own, fails the build."""

import re
import tarfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from kicad_bundle import homebrew, macbuilder, macho
from kicad_bundle.cache import HOMEBREW_BOTTLES, MACOS_SOURCES
from kicad_bundle.fetch import Fetch, add_dir, fetch_url, write_atomically

# KiCad's own files: its source (a separate release asset) covers them.
KICAD = re.compile(r"libki\w*\..+|kicad-cli|_\w+\.kiface")
BUILT = {re.compile(r"libwx_.+\.dylib"): "wxWidgets", re.compile(r"libngspice\..+"): "ngspice",
         re.compile(r"Python"): "Python"}
LICENCE = re.compile(r"(licen[cs]e|copying|copyright|notice|ftl|l?gpl|mpl|patents|unlicense)(v\d)?([-._ ].*)?", re.I)
# Acknowledgements some licences ask for in the documentation of binary distributions.
CREDITS = {
    "freetype": "Portions of this software are copyright © {year} The FreeType Project (https://freetype.org). "
                "All rights reserved.",
    "wxWidgets": "This software is based in part on the work of the Independent JPEG Group (wxWidgets' built-in "
                 "libjpeg).",
}

NOTICE = """kicad-cli {version} for macOS {arch}, repackaged from the official KiCad {version} DMG.

KiCad (KiCad.app/Contents/MacOS/kicad-cli, Contents/PlugIns/_eeschema.kiface and Contents/Frameworks/libki*)
is GPL-3.0-or-later. Its source is kicad-{version}-source.tar.gz, attached to the same GitHub release.

Every other library comes unmodified from the component listed below: a Homebrew bottle (the one
holding a file with the library's Mach-O UUID), or what KiCad's macOS builder, kicad-mac-builder,
builds itself. Each component's licence files are in KiCad.app/Contents/Resources/Licenses/<component>/.
The complete corresponding source of each component, with Homebrew's formula and patches, is in
{sources}, attached to the same release.
{credits}
file\tcomponent version\tfrom
{rows}
"""


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    folder: str  # in the sources archive
    licence_source: Path  # the archive its licence files come from
    sources: tuple[tuple[str, Path], ...]  # (name in its folder, file)


@dataclass
class ThirdParty:
    components: list[Component] = field(default_factory=list)
    rows: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)  # arch -> (file, component, from)
    licences: dict[str, dict[str, bytes]] = field(default_factory=dict)  # component -> its licence files
    year: str = ""  # of the KiCad release, for copyright credits


def licences(archive: Path) -> dict[str, bytes]:
    """Licence files of a source archive (one top folder): those at most one folder deep in it, or in
    a submodule its .gitmodules names, plus anything in a top-level LICENSES/."""
    with tarfile.open(archive) as tar:
        members = {m.name.split("/", 1)[1]: m for m in tar.getmembers() if m.isfile() and "/" in m.name}
        roots = [""]
        if gitmodules := members.get(".gitmodules"):
            text = tar.extractfile(gitmodules).read().decode()
            roots += [f"{p}/" for p in re.findall(r"^\s*path\s*=\s*(\S+)", text, re.M)]

        def wanted(rel: str) -> bool:
            for root in roots:
                parts = rel.removeprefix(root).split("/") if rel.startswith(root) else []
                if 0 < len(parts) <= 2 and (LICENCE.fullmatch(parts[-1]) or parts[0] == "LICENSES"):
                    return True
            return False

        chosen = sorted(((rel, m) for rel, m in members.items() if wanted(rel)), key=lambda rm: rm[1].offset_data)
        found = {rel: tar.extractfile(m).read() for rel, m in chosen}  # in archive order: no seeking back
    if not found:
        raise RuntimeError(f"{archive.name}: no licence file found")
    return found


def python_source(version: str, cache: Path, fetch: Fetch) -> Path:
    dest = cache / "python.org" / f"Python-{version}.tar.xz"
    if not dest.exists():
        data = fetch(f"https://www.python.org/ftp/python/{version}/Python-{version}.tar.xz")
        write_atomically(dest, lambda f: f.write(data))
    dest.touch()
    return dest


def _member(archive: Path, rel: str) -> bytes:
    with tarfile.open(archive) as tar:
        return tar.extractfile(next(m for m in tar if m.name.split("/", 1)[-1] == rel)).read()


def _require(needle: bytes, files: list[Path], what: str) -> None:
    if not any(needle in f.read_bytes() for f in files):
        raise RuntimeError(f"{what} doesn't match the bundled {sorted(f.name for f in files)}")


def _built(name: str, files: list[Path], pins, until: str, cache: Path, fetch: Fetch) -> tuple[Component, str]:
    """A component kicad-mac-builder builds, checked against the version string in its binaries."""
    builder = f"kicad-mac-builder {pins.builder[:10]}"
    if name == "Python":
        source = python_source(pins.python, cache, fetch)
        _require(pins.python.encode(), files, f"Python {pins.python}")
        return (Component(name, pins.python, f"Python-{pins.python}", source, ((source.name, source),)),
                f"python.org, made relocatable by {builder}")
    pin = pins.wxwidgets if name == "wxWidgets" else pins.ngspice
    sha = macbuilder.commit(pin, until, fetch)
    source = macbuilder.git_archive(pin, sha, name, cache / "git")
    if name == "wxWidgets":
        h = _member(source, "include/wx/version.h").decode()
        version = ".".join(re.search(rf"#define wx{k}\s+(\d+)", h).group(1)
                           for k in ("MAJOR_VERSION", "MINOR_VERSION", "RELEASE_NUMBER"))
        _require(f"wxWidgets {version}".encode("utf-32-le"), files, f"wxWidgets {version} ({pin.ref} at {sha[:10]})")
    else:
        version = pin.ref.removeprefix("ngspice-")
        _require(b"\0" + version.encode() + b"\0", files, f"ngspice {version} ({pin.ref})")
    return (Component(name, version, f"{name}-{version}-{sha[:12]}", source, ((source.name, source),)),
            f"{pin.url} {pin.ref} at {sha[:10]} ({builder})")


def collect(contents: Path, files: Iterable[Path], version: str, until: str, cache: Path,
            fetch: Fetch = fetch_url, archs: tuple[str, ...] = ("arm64", "x86_64")) -> ThirdParty:
    """The component of every file under a (universal) KiCad.app/Contents, KiCad's own aside, per
    architecture, with their sources fetched into cache. until: when KiCad published the release."""
    brewed: dict[str, list[Path]] = defaultdict(list)
    built: dict[str, list[Path]] = defaultdict(list)
    unknown = []
    for f in files:
        if KICAD.fullmatch(f.name):
            continue
        if name := homebrew.formula(f.name):
            brewed[name].append(f)
        elif name := next((n for p, n in BUILT.items() if p.fullmatch(f.name)), None):
            built[name].append(f)
        else:
            unknown.append(str(f.relative_to(contents)))
    if unknown:
        raise RuntimeError(f"libraries of unknown provenance, so their licence and source are unknown "
                           f"(add them to homebrew.FORMULAE if Homebrew ships them): {unknown}")

    third, rows = ThirdParty(), defaultdict(list)
    components: dict[tuple[str, str], Component] = {}
    for name, libs in sorted(brewed.items()):
        slices = {f: macho.slices(f.read_bytes()) for f in libs}
        for arch in archs:
            if missing := [f.name for f in libs if arch not in slices[f]]:
                raise RuntimeError(f"no {arch} code in {missing}")
            bottle = homebrew.match(name, arch, {f.name: slices[f][arch] for f in libs}, until,
                                    cache / HOMEBREW_BOTTLES, fetch)
            key = (name, bottle.version)
            if key not in components:
                srcs = tuple(homebrew.sources(bottle, cache / MACOS_SOURCES, fetch))
                components[key] = Component(name, bottle.version, f"{name}-{bottle.version}", srcs[0][1], srcs)
            origin = f"Homebrew {bottle.tag} bottle, homebrew-core {bottle.commit[:10]}"
            rows[arch] += [(str(f.relative_to(contents)), f"{name} {bottle.version}", origin) for f in libs]
    if built:
        pins = macbuilder.pins(version, until, fetch)
        for name, libs in sorted(built.items()):
            component, origin = _built(name, libs, pins, until, cache / MACOS_SOURCES, fetch)
            components[(name, component.version)] = component
            for arch in archs:
                rows[arch] += [(str(f.relative_to(contents)), f"{name} {component.version}", origin) for f in libs]
    third.components = sorted(components.values(), key=lambda c: (c.name, c.version))
    third.rows = {arch: sorted(r) for arch, r in rows.items()}
    third.licences = {c.name: licences(c.licence_source) for c in third.components}
    third.year = until[:4]
    return third


def write_notices(third: ThirdParty, root: Path, arch: str, version: str, sources: str) -> None:
    """root/THIRD-PARTY.txt, and each component's licence files under KiCad.app/Contents/Resources/Licenses/."""
    for name, files in third.licences.items():
        for rel, data in files.items():
            dest = root / "KiCad.app/Contents/Resources/Licenses" / name / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
    names = {c.name for c in third.components}
    credits = "".join(f"\n{CREDITS[n].format(year=third.year)}\n" for n in sorted(CREDITS) if n in names)
    rows = "\n".join(f"KiCad.app/Contents/{f}\t{c}\t{o}" for f, c, o in third.rows[arch])
    (root / "THIRD-PARTY.txt").write_text(NOTICE.format(version=version, arch=arch, sources=sources,
                                                        credits=credits, rows=rows))


def sources_archive(components: Iterable[Component], dest: Path) -> Path:
    """dest (a .tar) holding <dest stem>/<component folder>/<its source files>."""
    top = dest.name.removesuffix(".tar")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest, "w") as tar:  # the files are compressed already
        add_dir(tar, top)
        for c in components:
            add_dir(tar, f"{top}/{c.folder}")
            for name, path in c.sources:
                tar.add(path, arcname=f"{top}/{c.folder}/{name}")
    return dest
