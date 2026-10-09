"""Linux: official KiCad Docker image -> trimmed, relocatable kicad-cli folder (tar.gz).

KiCad publishes no relocatable Linux build, but every release gets an official image
(kicad/kicad:<version>, Debian, amd64). Its kicad-cli and the shared-library closure, minus glibc,
go into lib/; bin/kicad-cli is a wrapper that points the loader and KiCad's stock data there.
Every library but KiCad's own comes from a Debian package: the bundle ships each package's copyright
file, and <bundle>-sources.tar (a separate release asset) the exact Debian source of each.
"""

import os
import shutil
import subprocess
import tarfile
import tempfile
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path
from typing import BinaryIO

from kicad_bundle import debian, elf, linux_build, sch_import, simee_source, smoke
from kicad_bundle.bundle import KIFACES, archive
from kicad_bundle.cache import DEBIAN_SOURCES
from kicad_bundle.closure import closure

IMAGE = "kicad/kicad"
PLATFORM = "linux/amd64"
# The oldest host the bundle supports (glibc 2.39; the image's closure needs no newer symbols), bare,
# so the smoke test also proves nothing is missing from lib/.
SMOKE_IMAGE = "ubuntu:24.04"
ROOTS = ("usr/bin/kicad-cli", *(f"usr/bin/_{k}.kiface" for k in KIFACES))
# Data kicad-cli reads at startup (it logs an error without the API schema).
DATA = ("usr/share/kicad/schemas",)
# KiCad's own libraries (no Debian package owns them; the KiCad source covers them).
KICAD_LIBS = "libki*"
# What to unpack from the image: the roots, every library and the symlinks leading to them, and
# dpkg's records and copyright files to say which package each library comes from.
EXTRACT = (*ROOTS, "usr/lib/", "lib", "lib64", "etc/alternatives/", *(f"{d}/" for d in DATA),
           debian.DPKG_STATUS, f"{debian.DPKG_INFO}/", "usr/share/doc/")

NOTICE = """kicad-cli {version} for Linux x86_64, repackaged from the official kicad/kicad:{version} Docker image.{simee}

KiCad (libexec/kicad-cli, libexec/*.kiface and lib/{kicad_libs}) is GPL-3.0-or-later. Its source is
kicad-{version}-source.tar.gz, attached to the same GitHub release.

Every other file in lib/ comes unmodified from the Debian package listed below. Each package's
licence and copyright notices are in share/doc/<package>/copyright. The complete corresponding
source of each package, exactly as Debian built it, is in
{sources}, attached to the same release:
unpack it and run `dpkg-source -x <source>_<version>/*.dsc` (the version without its epoch).

file\tpackage version\tsource version
{rows}
"""

WRAPPER = """#!/bin/sh
# kicad-cli from the official KiCad Docker image, using the shared libraries and data in this bundle.
here=$(dirname "$(dirname "$(readlink -f "$0")")")
export LD_LIBRARY_PATH="$here/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export KICAD_STOCK_DATA_HOME="${KICAD_STOCK_DATA_HOME:-$here/share/kicad}"
exec "$here/libexec/kicad-cli" "$@"
"""


def _docker() -> str:
    if found := shutil.which("docker"):
        return found
    raise RuntimeError("docker is needed to unpack the KiCad image and smoke-test on Linux")


def _wanted(member: tarfile.TarInfo) -> bool:
    name = member.name.removeprefix("./")
    if not any(name == e.rstrip("/") or (e.endswith("/") and name.startswith(e)) for e in EXTRACT):
        return False
    if member.islnk():
        return _wanted(tarfile.TarInfo(member.linkname))
    return member.isfile() or member.isdir() or member.issym()


def extract(stream: BinaryIO, dest: Path) -> None:
    """Unpack the parts of an image filesystem tar (a stream, e.g. `docker export`) that EXTRACT names.
    Symlinks are kept as they are; elf.resolve_in follows them inside dest."""
    dest.mkdir(parents=True, exist_ok=True)
    trusted = {"filter": "fully_trusted"} if hasattr(tarfile, "fully_trusted_filter") else {}
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for member in tar:
            if _wanted(member):
                tar.extract(member, dest, **trusted)


def write_wrapper(path: Path) -> None:
    path.write_text(WRAPPER)
    path.chmod(0o755)


def sources_name(root: Path) -> str:
    return f"{root.name}-sources.tar"


SIMEE_NOTICE = """
KiCad's own files (libexec/, lib/{kicad_libs}) are KiCad {version} with simee's changes, built on that image
with its Debian libraries from simee-kicad commit {sha}."""


def assemble(rootfs: Path, root: Path, version: str, simee_sha: str | None = None) -> set[tuple[str, str]]:
    """root/{bin/kicad-cli (wrapper), libexec/ (kicad-cli + kifaces), lib/ (closure), share/kicad/,
    share/doc/<package>/copyright, THIRD-PARTY.txt}. Each library is stored under the DT_NEEDED
    name(s) the loader looks it up by. Returns the Debian (source, version)s the libraries come from."""
    names: dict[Path, set[str]] = defaultdict(set)
    base = elf.make_resolver(rootfs)

    def resolve(name: str, binary: Path) -> Path | None:
        found = base(name, binary)
        if found is not None:
            names[found].add(name)
        return found

    roots = [elf.resolve_in(rootfs, r) for r in ROOTS]
    missing: list[tuple[str, str]] = []
    keep = closure(roots, deps=elf.deps, resolve=resolve, missing=missing)
    if missing:
        raise RuntimeError(f"unresolved libraries: {missing}")

    libs = sorted(keep - set(roots))
    owned, unowned = debian.provenance(rootfs, libs)
    if strays := [lib.name for lib in unowned if not fnmatch(lib.name, KICAD_LIBS)]:
        raise RuntimeError(f"libraries no Debian package owns, so their licence and source are unknown: {strays}")

    for d in ("bin", "libexec", "lib"):
        (root / d).mkdir(parents=True, exist_ok=True)
    for src, rel in zip(roots, ROOTS):
        shutil.copy2(src, root / "libexec" / Path(rel).name)
    rows = []
    for lib in libs:
        first, *others = sorted(names[lib])
        shutil.copy2(lib, root / "lib" / first)
        for other in others:
            os.symlink(first, root / "lib" / other)
        if pkg := owned.get(lib):
            rows.append(f"lib/{first}\t{pkg.name} {pkg.version}\t{pkg.source} {pkg.source_version}")
    for pkg in {p.name: p for p in owned.values()}.values():
        dest = root / "share/doc" / pkg.name / "copyright"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(elf.resolve_in(rootfs, f"usr/share/doc/{pkg.name}/copyright"), dest)
    simee = SIMEE_NOTICE.format(kicad_libs=KICAD_LIBS, sha=simee_sha, version=version) if simee_sha else ""
    (root / "THIRD-PARTY.txt").write_text(NOTICE.format(version=version, kicad_libs=KICAD_LIBS, simee=simee,
                                                        sources=sources_name(root), rows="\n".join(sorted(rows))))
    for data in DATA:
        shutil.copytree(elf.resolve_in(rootfs, data), root / "share" / Path(data).relative_to("usr/share"),
                        symlinks=True)
    write_wrapper(root / "bin" / "kicad-cli")
    print(f"  {len(keep)} files from the image, {len(owned)} of them from {len(set(owned.values()))} Debian packages")
    return {(p.source, p.source_version) for p in owned.values()}


def smoke_command(root: Path) -> list[str]:
    """Run the bundle's kicad-cli in a bare SMOKE_IMAGE container; smoke.check's paths (the bundle, the
    test schematic, its temp dir) are mounted at the same paths and its KICAD_* homes passed through."""
    root = root.resolve()
    mounts = [f"{root}:{root}:ro", f"{smoke.SCHEMATIC.parent}:{smoke.SCHEMATIC.parent}:ro",
              f"{tempfile.gettempdir()}:{tempfile.gettempdir()}"]
    return ["docker", "run", "--rm", "--platform", PLATFORM, "--user", f"{os.getuid()}:{os.getgid()}",
            *(a for m in mounts for a in ("-v", m)), *(a for k in smoke.KICAD_HOMES for a in ("-e", k)),
            SMOKE_IMAGE, str(root / "bin" / "kicad-cli")]


def _export_image(version: str, rootfs: Path) -> str:
    """Unpack the image's EXTRACT parts into rootfs; returns its digest."""
    docker, image = _docker(), f"{IMAGE}:{version}"
    subprocess.run([docker, "pull", "--quiet", "--platform", PLATFORM, image], check=True)
    digest = subprocess.run([docker, "image", "inspect", "--format", "{{index .RepoDigests 0}}", image],
                            capture_output=True, text=True, check=True).stdout.strip()
    print(f"  image {digest}")
    container = subprocess.run([docker, "create", "--platform", PLATFORM, image],
                               capture_output=True, text=True, check=True).stdout.strip()
    try:
        with subprocess.Popen([docker, "export", container], stdout=subprocess.PIPE) as proc:
            extract(proc.stdout, rootfs)
        if proc.returncode:
            raise RuntimeError(f"docker export failed ({proc.returncode})")
    finally:
        subprocess.run([docker, "rm", container], check=False, capture_output=True)
    return digest


def package(version: str, out_dir: Path, cache: Path, work: Path, run_smoke: bool = True,
            simee_ref: str | None = None) -> list[Path]:
    """The bundle and its Debian sources. cache holds the sources; docker keeps the pulled image.
    With simee_ref (a simee-kicad branch such as simee/10.0.6), KiCad's own files are built from it
    (linux_build), and its source tarball is one of the results."""
    rootfs = work / "linux-image"
    root = work / f"kicad-cli-{version}-linux-x86_64"
    for d in (rootfs, root):
        if d.exists():
            shutil.rmtree(d)
    digest = _export_image(version, rootfs)
    sha = simee_source.resolve_ref(simee_ref) if simee_ref else None
    sources = assemble(rootfs, root, version, sha)
    extra = []
    if sha:
        print(f"  building KiCad's own files from simee-kicad {sha} ({simee_ref})")
        src = simee_source.source_archive(sha, out_dir / f"kicad-{version}-source.tar.gz")
        print("  replaced " + ", ".join(linux_build.overlay(root, linux_build.build(digest, rootfs, src, work))))
        extra.append(src)
    if run_smoke:
        smoke.check(smoke_command(root))
        if sha:
            for fixture in sch_import.fixtures():
                sch_import.check(smoke_command(root), fixture)
        print(f"  smoke test passed ({SMOKE_IMAGE}{', sch import' if sha else ''})")
    bundle = archive(root, out_dir, "tar.gz")
    return [bundle, debian.sources_archive(sources, out_dir / sources_name(root), cache / DEBIAN_SOURCES), *extra]
