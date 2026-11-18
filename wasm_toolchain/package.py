"""Trim wasi-sdk's release for one host to what compiles and links a C++ model into a wasm32-wasip1 module with
simee-core's flags (simee_add_wasm_module): clang under its own names, lld as wasm-ld, the shared libraries those
two load, clang's own headers and builtins for the target, and the target's sysroot with C++ exceptions (of ~600 MB,
~150 MB); add the licences of what is kept, and archive it. wasi-sdk's files are copied unchanged: clang finds its
headers, builtins and lld relative to itself, and its clang.cfg names the sysroot relative to itself, so the trimmed
root runs wherever it is copied, like the full one."""

import hashlib
import os
import tarfile
import zipfile
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Callable

from wasm_toolchain import components
from wasm_toolchain.components import BINARIES, COMPONENTS, MSVC_RUNTIME, VERSION, Component
from kicad_bundle import bundle, elf, macho, pe, toolchain_trim
from kicad_bundle.closure import closure
from kicad_bundle.fetch import Fetch, fetch_url
from kicad_bundle.gnu_build import REPO
from kicad_bundle.windows_third_party import MSVC, MSVC_TERMS

TARGET = "wasm32-wasip1"  # the only target simee builds for: the modules run under WASI preview 1
SYSROOT = "share/wasi-sysroot"
# The programs (each with .exe on Windows): clang-<major> is the compiler, clang and clang++ name it and pick its C
# or C++ driver by their name, wasm-ld names lld. They are links, except on Windows, where wasi-sdk ships each name
# as a copy of the program (clang.exe and clang++.exe, lld.exe and wasm-ld.exe), which this packages as hard links.
# clang.cfg and clang++.cfg are wasi-sdk's configuration of each driver name (the sysroot, relative to the program).
PROGRAMS = ("clang-[0-9]*", "clang", "lld")
NAMES = ("clang++", "wasm-ld")
KEEP = ("VERSION",  # the upstream commits it is built from
        *(f"bin/{p}{exe}" for p in (*PROGRAMS, *NAMES) for exe in ("", ".exe")), "bin/clang.cfg", "bin/clang++.cfg",
        "lib/clang/*/include", f"lib/clang/*/lib/{TARGET.replace('-', '-unknown-')}",  # compiler-rt's builtins
        f"{SYSROOT}/include/{TARGET}", f"{SYSROOT}/lib/{TARGET}")
# Of the sysroot: the build without exception handling (noeh/; eh/ has libc++ with it, which the modules use) and
# the LTO builds of its libraries (llvm-lto/, at both levels), which simee's flags never link.
DROP = (f"{SYSROOT}/include/{TARGET}/noeh", f"{SYSROOT}/lib/{TARGET}/noeh", f"{SYSROOT}/lib/{TARGET}/llvm-lto",
        f"{SYSROOT}/lib/{TARGET}/eh/llvm-lto")
# clang searches the sysroot's include/c++/v1 for C++ headers only if it exists; the eh/ headers come after it.
EMPTY_DIRS = (f"{SYSROOT}/include/c++/v1",)
# The shared libraries the programs may load: only those their closure reaches are kept.
LIBRARIES = ("lib/*.dylib", "lib/*.so", "lib/*.so.*", "bin/*.dll", "lib/*.dll")
# What wasi-sdk's Linux programs load from the system besides glibc, as its own release does: GCC's C++ runtime, and
# ncurses' terminfo (libedit's); every Debian and Ubuntu has them (apt needs the first two, bash the third), and the
# check runs in a bare ubuntu:24.04.
LINUX_SYSTEM = {"libstdc++.so.6", "libgcc_s.so.1", "libtinfo.so.6"}


def kept(rel: str) -> bool:
    """Whether the file at rel (below the release's top folder) is kept whatever the programs load."""
    return toolchain_trim.matches(rel, KEEP) and not toolchain_trim.matches(rel, DROP)


def is_library(rel: str) -> bool:
    return toolchain_trim.matches(rel, LIBRARIES)


def _programs(root: Path) -> list[Path]:
    """The programs' files in root/bin (not the links naming them)."""
    return sorted(p for p in (root / "bin").iterdir() if not p.is_symlink()
                  and any(fnmatchcase(p.name, f"{g}{exe}") for g in PROGRAMS for exe in ("", ".exe")))


def _is_pe(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(2) == b"MZ"


def _resolver(root: Path) -> Callable[[str, Path], Path | None]:
    """A library name a program or library loads -> its file in root, or None for one of the OS's own. A name
    neither shipped nor the OS's resolves to a path that doesn't exist, which closure() reports as missing."""
    def resolve(name: str, binary: Path) -> Path | None:
        if _is_pe(binary):
            # Next to the program, else Windows' own (KERNEL32, the UCRT, ...); Microsoft's C++ runtime isn't.
            dlls = {p.name.lower(): p for p in binary.parent.iterdir()}
            if found := dlls.get(name.lower()):
                return found
            return binary.parent / name if MSVC.fullmatch(name) else None
        if macho.is_macho(binary):
            return None if name.startswith(macho.SYSTEM_PREFIXES) else root / "lib" / name.rsplit("/", 1)[-1]
        if name in elf.GLIBC | LINUX_SYSTEM or name in {a.loader for a in elf.ARCHES.values()}:
            return None
        return root / "lib" / name

    return resolve


def _loads(binary: Path) -> list[str]:
    if _is_pe(binary):
        return pe.deps(binary)
    if macho.is_macho(binary):
        return macho.loads(binary.read_bytes())
    return elf.deps(binary)


def libraries(root: Path) -> set[Path]:
    """The shared libraries (as paths in root, the links naming them included) the programs load, directly or not."""
    programs = _programs(root)
    missing: list[tuple[str, str]] = []
    reached = closure(programs, _loads, _resolver(root), missing=missing)
    if missing:
        raise RuntimeError("not in the release: " + ", ".join(f"{b} loads {n}" for b, n in sorted(missing)))
    found = set()
    for path in reached - set(programs):
        found.add(path)
        while path.is_symlink():  # the name loaded, and each link to the file
            path = path.parent / path.readlink()
            found.add(path)
    return found


def _linker_version(program: Path) -> tuple[int, int]:
    import pefile
    p = pefile.PE(str(program), fast_load=True)
    try:
        return p.OPTIONAL_HEADER.MajorLinkerVersion, p.OPTIONAL_HEADER.MinorLinkerVersion
    finally:
        p.close()


def add_msvc_runtime(root: Path, runtime: Path) -> list[str]:
    """Copy the files of Microsoft's C++ runtime the Windows programs load into root/bin, from runtime (kicad-cli's
    Windows bundle), each checked to be Microsoft's and at least the version of the toolset the programs were linked
    with; their names."""
    programs = [p for p in _programs(root) if _is_pe(p)]
    wanted = sorted({n.lower() for p in programs for n in pe.deps(p) if MSVC.fullmatch(n)})
    if not wanted:
        return []
    toolset = max(map(_linker_version, programs))
    with zipfile.ZipFile(runtime) as z:
        by_name = {Path(n).name.lower(): n for n in z.namelist()}
        for name in wanted:
            if name not in by_name:
                raise RuntimeError(f"{runtime.name} has no {name}, which the programs load")
            (root / "bin" / name).write_bytes(z.read(by_name[name]))
    for name in wanted:
        info = pe.version_info(root / "bin" / name)
        version = tuple(int(v) for v in info.get("FileVersion", "0.0").split(".")[:2])
        if info.get("CompanyName") != "Microsoft Corporation" or version < toolset:
            raise RuntimeError(f"{name} from {runtime.name} is {info.get('CompanyName')}'s {info.get('FileVersion')},"
                               f" not Microsoft's C++ runtime for MSVC {toolset[0]}.{toolset[1]} or later")
    return wanted


def link_copies(root: Path) -> None:
    """Make the programs of root/bin that are byte for byte the same hard links to one of them: on Windows, where
    wasi-sdk ships each of a program's names as a copy (60 MB for clang), the archive then holds each program once."""
    first: dict[str, Path] = {}
    names = [f"{g}{exe}" for g in (*PROGRAMS, *NAMES) for exe in ("", ".exe")]
    for path in sorted(p for p in (root / "bin").iterdir() if p.is_file() and not p.is_symlink()
                       and any(fnmatchcase(p.name, n) for n in names)):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if (same := first.setdefault(digest, path)) != path and not path.samefile(same):
            path.unlink()
            os.link(same, path)


def extract(archive: Path, work: Path, name: str, msvc_runtime: Callable[[], Path] | None = None) -> Path:
    """The kept files of wasi-sdk's archive in work/name, with the shared libraries the programs load (on Windows,
    Microsoft's C++ runtime from msvc_runtime()); returns that root."""
    root = work / name
    if root.exists():
        bundle.remove(root)
    root.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        top = toolchain_trim.top([m.name for m in members])
        tar.extractall(root, filter="tar",
                       members=toolchain_trim.kept_members(members, top, lambda rel: kept(rel) or is_library(rel)))
    for rel in EMPTY_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    if msvc_runtime and any(_is_pe(p) for p in _programs(root)):
        add_msvc_runtime(root, msvc_runtime())
    needed = libraries(root)
    for path in [p for p in root.rglob("*") if is_library(p.relative_to(root).as_posix())]:
        if path not in needed:
            path.unlink()
    link_copies(root)
    return root


def shipped(root: Path) -> list[Component]:
    """The components whose files root holds: every one always shipped, and those whose libraries the programs
    load. A library none of them is the source of fails."""
    loaded = [p.name for p in root.rglob("*") if is_library(p.relative_to(root).as_posix())]
    if unknown := [n for n in loaded if not any(fnmatchcase(n.lower(), g.lower())
                                                for c in COMPONENTS for g in c.libraries)]:
        raise RuntimeError(f"no component is the source of {', '.join(sorted(unknown))}: add it to components.py")
    return [c for c in COMPONENTS
            if c.always or any(fnmatchcase(n.lower(), g.lower()) for n in loaded for g in c.libraries)]


def _licence(licence: components.Licence, cache: Path, get: Fetch) -> bytes:
    path = components.fetch(licence.source, cache, get)
    if not tarfile.is_tarfile(path):
        return path.read_bytes()
    with tarfile.open(path) as tar:
        top = toolchain_trim.top(tar.getnames())
        return tar.extractfile(f"{top}{licence.path}").read()


def write_licences(found: list[Component], root: Path, cache: Path, get: Fetch = fetch_url) -> None:
    """root/share/doc/<component>/<path> for each licence file of each component found."""
    for c in found:
        for licence in c.licences:
            dest = root / "share" / "doc" / c.name / licence.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(_licence(licence, cache, get))


NOTICE = """\
wasi-sdk {version} for {host}: the WebAssembly project's own build, unmodified, from

\t{url}
\tsha256 {sha256}

trimmed by simee ({repo})
to what compiles and links C++ into a {target} module: clang (as clang and clang++), lld (as wasm-ld),
the libraries they load, clang's headers and builtins for {target}, and the {target} sysroot
(wasi-libc, and libc++, libc++abi and libunwind built with C++ exceptions). The VERSION file next to this one
names the commits of llvm-project and wasi-libc it is built from.

Components (each one's licence files are in share/doc/<folder>/, from its source at those commits):

"""
LINUX = """
The Linux programs load glibc, GCC's libstdc++ and libgcc_s and ncurses' libtinfo from the system, as wasi-sdk's
own build does.
"""
MICROSOFT = """
Microsoft's Visual C++ runtime ({files}, in bin/) is redistributed unmodified, the same files simee's kicad-cli
bundle ships ({source}), under the Distributable Code terms of Visual Studio 2022 ("Visual C++ Runtime Files"):
{terms}
It is Microsoft's and not open source. The Universal CRT isn't shipped: Windows 10 and later always use their own.
"""


def notice(host: str, found: list[Component], msvc: list[str] = ()) -> str:
    b = BINARIES[host]
    rows = "".join(f"\t{c.name}\t{c.what}\t{c.licence}\n" for c in found)
    return (NOTICE.format(version=VERSION, host=host, url=b.url, sha256=b.sha256, repo=REPO, target=TARGET) + rows
            + (LINUX if host.startswith("linux") else "")
            + (MICROSOFT.format(files=", ".join(msvc), source=MSVC_RUNTIME.url, terms=MSVC_TERMS) if msvc else ""))


FORMAT = "tar.xz"


def package(root: Path, host: str, cache: Path, out: Path, get: Fetch = fetch_url) -> Path:
    """Add the licences and THIRD-PARTY.txt to root, and archive it into out."""
    found = shipped(root)
    write_licences(found, root, cache, get)
    msvc = sorted(p.name for p in (root / "bin").iterdir() if MSVC.fullmatch(p.name))
    (root / "THIRD-PARTY.txt").write_text(notice(host, found, msvc))
    return bundle.archive(root, out, FORMAT)


def make(host: str, cache: Path, work: Path, out: Path, get: Fetch = fetch_url) -> Path:
    """Download (or reuse) wasi-sdk's archive for host, and package the trimmed toolchain."""
    root = extract(components.fetch(BINARIES[host], cache, get), work, components.name(host),
                   lambda: components.fetch(MSVC_RUNTIME, cache, get))
    return package(root, host, cache, out, get)
