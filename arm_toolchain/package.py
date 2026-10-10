"""Trim Arm's toolchain release for one host to what an RP2040 build runs and links (of ~1 GB, ~185 MB),
add the licences of what that keeps from Arm's source snapshot, and archive it. Arm's files are copied
unchanged: GCC finds its programs, binutils, headers and libraries relative to bin/arm-none-eabi-gcc,
so the trimmed root runs wherever it is copied, like the full one."""

import tarfile
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path

from arm_toolchain import components
from arm_toolchain.components import BINARIES, MULTILIBS, RELEASE, SHIPPED, SOURCE
from kicad_bundle import bundle, third_party
from kicad_bundle.gnu_build import REPO

# What is kept, as paths below the release's top folder; a pattern naming a folder keeps all of it.
# The C and C++ drivers, binutils, the compilers and LTO, and the headers of newlib, libstdc++ and GCC.
PROGRAMS = tuple(f"bin/arm-none-eabi-{p}*" for p in (
    "gcc", "g++", "cpp", "as", "ld", "ar", "ranlib", "nm", "objcopy", "objdump", "readelf", "size", "strip",
    "addr2line"))
KEEP = (*PROGRAMS,
        *(f"libexec/gcc/arm-none-eabi/*/{p}*" for p in ("cc1", "collect2", "lto", "liblto_plugin")),
        "arm-none-eabi/bin", "arm-none-eabi/include",
        "arm-none-eabi/lib/*.specs",  # --specs=nosys.specs etc., which GCC looks for here, not in the multilib's
        "lib/gcc/arm-none-eabi/*/include", "lib/gcc/arm-none-eabi/*/include-fixed",
        "*manifest.txt")  # Arm's record of how it configured each component


def _multilib(multilib: str) -> tuple[str, ...]:
    """newlib, libstdc++ and their specs, and libgcc with its start files, for one multilib."""
    return f"arm-none-eabi/lib/{multilib}", f"lib/gcc/arm-none-eabi/*/{multilib}"


def kept(rel: str, multilibs: tuple[str, ...] = MULTILIBS) -> bool:
    """Whether the file at rel (below the release's top folder) is kept: its path, or a folder above
    it, matches a pattern, one path component at a time."""
    parts = rel.split("/")
    for pattern in (*KEEP, *(p for m in multilibs for p in _multilib(m))):
        wanted = pattern.split("/")
        if len(parts) >= len(wanted) and all(map(fnmatchcase, parts, wanted)):
            return True
    return False


def _top(names: list[str]) -> str:
    """The archive's one top folder, with a trailing /, or "" (Arm's Windows zip has none)."""
    tops = {n.partition("/")[0] for n in names}
    return f"{tops.pop()}/" if len(tops) == 1 else ""


def extract(archive: Path, work: Path, name: str) -> Path:
    """The kept files of Arm's archive (a tar or zip) in work/name; returns that root."""
    root = work / name
    if root.exists():
        bundle.remove(root)
    root.mkdir(parents=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            top = _top(z.namelist())
            for info in z.infolist():
                rel = info.filename.removeprefix(top)
                if not info.is_dir() and kept(rel):
                    (root / rel).parent.mkdir(parents=True, exist_ok=True)
                    (root / rel).write_bytes(z.read(info))
        return root
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        top = _top([m.name for m in members])
        # A hard link names its target by its path in the archive (a symbolic one relative to itself).
        tar.extractall(root, filter="tar", members=[
            m.replace(name=m.name.removeprefix(top), deep=False,
                      linkname=m.linkname.removeprefix(top) if m.islnk() else m.linkname)
            for m in members if not m.isdir() and kept(m.name.removeprefix(top))])
    return root


def write_licences(snapshot: Path, root: Path) -> None:
    """root/share/doc/<component>/<its licence files> for each SHIPPED component of Arm's source snapshot."""
    third_party.write_licences(third_party.folder_licences(snapshot, SHIPPED), root / "share" / "doc")


NOTICE = """\
Arm GNU Toolchain {release} for arm-none-eabi, {host}: Arm's own build, unmodified, from

\t{url}
\tsha256 {sha256}

trimmed by simee ({repo}) to what a Cortex-M0+ build runs
and links: the C and C++ drivers, binutils, cc1, cc1plus and LTO, and newlib, libstdc++ and libgcc for
the {multilibs} multilib(s).
The manifest file next to this one is Arm's record of how it configured each component.

"""
COMPONENTS = """\
Components (each one's licence files are in share/doc/<folder>/, from Arm's source snapshot):

\tbinutils-gdb\tbinutils (as, ld, objcopy, ...)\tGPL-3.0-or-later
\tgcc\tGCC: the drivers, cc1, cc1plus, lto1\tGPL-3.0-or-later
\t\tlibgcc, libstdc++ (linked into firmware)\tGPL-3.0-or-later with the GCC Runtime Library Exception 3.1
\tnewlib-cygwin\tnewlib's libc, libm and libnosys (linked into firmware)\tBSD-style and similar, see COPYING.NEWLIB
\tgmp, mpfr, mpc\tlinked into the compilers\tLGPL-3.0-or-later
\tisl\tlinked into the compilers\tMIT
\tlibiconv\tcharacter set conversion in the compilers\tLGPL-2.1-or-later
"""
SOURCES = """
The GitHub release this archive comes from also holds {source}, Arm's
source snapshot for this release, which the programs and libraries above are built from:
{source_url}
"""
MINGW = """
The Windows programs are linked with MinGW-w64's runtime (as Arm built them): its licence is at
https://sourceforge.net/p/mingw-w64/mingw-w64/ci/master/tree/COPYING.MinGW-w64-runtime/COPYING.MinGW-w64-runtime.txt
"""


def notice(host: str) -> str:
    b = BINARIES[host]
    return (NOTICE.format(release=RELEASE, host=host, url=b.url, sha256=b.sha256, repo=REPO,
                          multilibs=", ".join(MULTILIBS))
            + COMPONENTS + (MINGW if host.startswith("windows") else "")
            + SOURCES.format(source=SOURCE.archive, source_url=SOURCE.url))


def package(root: Path, snapshot: Path, out: Path, text: str, fmt: str) -> Path:
    """Add the licences and THIRD-PARTY.txt (text) to root, and archive it into out as fmt."""
    write_licences(snapshot, root)
    (root / "THIRD-PARTY.txt").write_text(text)
    return bundle.archive(root, out, fmt)


def make(host: str, cache: Path, work: Path, out: Path) -> Path:
    """Download (or reuse) Arm's archive for host and the source snapshot, and package the trimmed toolchain."""
    root = extract(components.fetch(BINARIES[host], cache), work, components.name(host))
    return package(root, components.fetch(SOURCE, cache), out, notice(host), BINARIES[host].format)
