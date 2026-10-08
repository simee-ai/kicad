"""What the macOS and Windows bundles share about third-party components: a component's sources, the
licence files found in its source archive, and the archive of every component's sources."""

import re
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from kicad_bundle.fetch import add_dir

LICENCE = re.compile(r"(licen[cs]e|copying|copyright|notice|ftl|l?gpl|mpl|patents|unlicense)(v\d)?([-._ ].*)?", re.I)

# Acknowledgements some licences ask for in the documentation of binary distributions.
CREDITS = {
    "freetype": "Portions of this software are copyright © {year} The FreeType Project (https://freetype.org). "
                "All rights reserved.",
    "libjpeg-turbo": "This software is based in part on the work of the Independent JPEG Group.",
    "wxWidgets": "This software is based in part on the work of the Independent JPEG Group (wxWidgets' built-in "
                 "libjpeg).",
}


@dataclass(frozen=True)
class Component:
    name: str
    version: str
    folder: str  # in the sources archive
    licence_source: Path  # the archive its licence files come from
    sources: tuple[tuple[str, Path], ...]  # (name in its folder, file)


def _read(archive: Path, wanted: Callable[[str], bool]) -> dict[str, bytes]:
    """The files of a source archive (tar of any compression, or zip) whose path below the archive's
    top folder wanted() accepts, by that path."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            infos = {i.filename.split("/", 1)[1]: i for i in z.infolist() if not i.is_dir() and "/" in i.filename}
            return {rel: z.read(i) for rel, i in infos.items() if wanted(rel)}
    with tarfile.open(archive) as tar:
        members = {m.name.split("/", 1)[1]: m for m in tar.getmembers() if m.isfile() and "/" in m.name}
        chosen = sorted(((rel, m) for rel, m in members.items() if wanted(rel)),
                        key=lambda rm: rm[1].offset_data)
        return {rel: tar.extractfile(m).read() for rel, m in chosen}  # in archive order: no seeking back


def licences(archive: Path) -> dict[str, bytes]:
    """Licence files of a source archive (one top folder): those at most one folder deep in it, or in
    a submodule its .gitmodules names, plus anything in a top-level LICENSES/."""
    gitmodules = _read(archive, lambda rel: rel == ".gitmodules").get(".gitmodules", b"")
    roots = ["", *(f"{p}/" for p in re.findall(r"^\s*path\s*=\s*(\S+)", gitmodules.decode(), re.M))]

    def wanted(rel: str) -> bool:
        for root in roots:
            parts = rel.removeprefix(root).split("/") if rel.startswith(root) else []
            if 0 < len(parts) <= 2 and (LICENCE.fullmatch(parts[-1]) or parts[0] == "LICENSES"):
                return True
        return False

    found = _read(archive, wanted)
    if not found:
        raise RuntimeError(f"{archive.name}: no licence file found")
    return found


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


def credits(names: Iterable[str], year: str) -> str:
    """The CREDITS of the components named, for a notice (year: of the KiCad release)."""
    names = set(names)
    return "".join(f"\n{CREDITS[n].format(year=year)}\n" for n in sorted(CREDITS) if n in names)


def write_licences(licences: dict[str, dict[str, bytes]], dest: Path) -> None:
    """dest/<component>/<path in its source> for each component's licence files."""
    for name, files in licences.items():
        for rel, data in files.items():
            path = dest / name / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
