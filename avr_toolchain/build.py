"""Build the AVR toolchain (binutils, GCC with GMP/MPFR/MPC in its tree, avr-libc) for one host into a
root that runs from any folder: GCC finds cc1, as, ld and avr-libc relative to its own binary.

A host this machine can't run natively is cross-built ("Canadian cross": built here, runs there,
compiles for the AVR). That needs an AVR toolchain for this machine to build the target libraries
(libgcc, avr-libc), so the native host is built first: macos-x86_64 on an arm64 Mac after macos-arm64,
windows-x86_64 on Linux (with mingw-w64) after linux-x86_64.
"""

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from avr_toolchain import components
from avr_toolchain.components import AVR_LIBC, BINUTILS, GCC, IN_TREE, SOURCES
from kicad_bundle import bundle, debian

MACOS_MIN = "11.0"  # the first macOS on Apple silicon
BUGURL = "https://github.com/simee-ai/simee-kicad/issues"


@dataclass(frozen=True)
class Host:
    name: str
    triple: str
    os: str  # sys.platform and platform.machine() of a machine that builds it natively ("": none)
    cpu: str
    cross_env: dict[str, str]  # the compilers that build it on another machine
    env: dict[str, str] = field(default_factory=dict)
    configure: tuple[str, ...] = ()  # more options for binutils and GCC
    ldflags: str = ""  # links the C/C++ runtimes statically, so the toolchain needs nothing the OS lacks
    runtime_files: tuple[str, ...] = ()  # what that links in, by the name `$CXX -print-file-name` takes
    exe: str = ""
    archive: str = "tar.gz"

    def native(self, os_: str, cpu: str) -> bool:
        return (self.os, self.cpu) == (os_, cpu)


_GNU_RUNTIME = ("libstdc++.a", "libgcc.a", "libgcc_eh.a")
_MACOS = {"MACOSX_DEPLOYMENT_TARGET": MACOS_MIN}
# The OS's own libz: GCC's bundled zlib doesn't compile against current macOS SDKs (its fdopen macro).
_MACOS_CONFIGURE = ("--with-system-zlib",)
# Xcode's tools handle every architecture, and there are no <triple>-ar etc. for configure to find.
_MACOS_TOOLS = {"AR": "ar", "RANLIB": "ranlib", "NM": "nm", "STRIP": "strip"}
HOSTS = {h.name: h for h in (
    Host("macos-arm64", "aarch64-apple-darwin", "darwin", "arm64",
         {**_MACOS_TOOLS, "CC": "clang -arch arm64", "CXX": "clang++ -arch arm64"}, _MACOS, _MACOS_CONFIGURE),
    Host("macos-x86_64", "x86_64-apple-darwin", "darwin", "x86_64",
         {**_MACOS_TOOLS, "CC": "clang -arch x86_64", "CXX": "clang++ -arch x86_64"}, _MACOS, _MACOS_CONFIGURE),
    Host("linux-x86_64", "x86_64-linux-gnu", "linux", "x86_64",
         {"CC": "x86_64-linux-gnu-gcc", "CXX": "x86_64-linux-gnu-g++"},
         ldflags="-static-libstdc++ -static-libgcc", runtime_files=_GNU_RUNTIME),
    Host("windows-x86_64", "x86_64-w64-mingw32", "", "",
         {"CC": "x86_64-w64-mingw32-gcc", "CXX": "x86_64-w64-mingw32-g++"}, ldflags="-static",
         runtime_files=(*_GNU_RUNTIME, "crt2.o", "libmingw32.a", "libmingwex.a", "libmsvcrt.a", "libmoldname.a",
                        "libkernel32.a"),
         exe=".exe", archive="zip"),
)}


def this_machine() -> tuple[str, str]:
    return sys.platform, platform.machine()


def native_host() -> Host | None:
    return next((h for h in HOSTS.values() if h.native(*this_machine())), None)


def runnable(host: Host) -> bool:
    """Whether this machine runs host's binaries: its own, or Intel macOS ones under Rosetta."""
    return host.native(*this_machine()) or (host.os == sys.platform == "darwin")


def _host_args(host: Host, cross_from: str | None) -> list[str]:
    return [f"--build={cross_from}", f"--host={host.triple}"] if cross_from else []


def binutils_args(host: Host, prefix: Path, cross_from: str | None) -> list[str]:
    """cross_from: this machine's triple when cross-building host, else None."""
    return ["--target=avr", f"--prefix={prefix}", *_host_args(host, cross_from), "--disable-nls", "--disable-werror",
            "--disable-gdb", "--disable-gdbserver", "--disable-sim", "--disable-readline", "--disable-libdecnumber",
            "--disable-gprofng", "--without-zstd", "--without-debuginfod", *host.configure]


def gcc_args(host: Host, prefix: Path, cross_from: str | None) -> list[str]:
    # No --with-as/--with-ld: GCC then finds the assembler and linker relative to itself (avr/bin/).
    return ["--target=avr", f"--prefix={prefix}", *_host_args(host, cross_from), "--enable-languages=c,c++",
            "--with-avrlibc", "--with-dwarf2", "--disable-nls", "--disable-libssp", "--disable-shared",
            "--disable-threads", "--disable-libgomp", "--disable-libcc1", "--disable-plugin", "--without-isl",
            "--without-zstd", f"--with-pkgversion=simee avr-gcc {GCC.version}, avr-libc {AVR_LIBC.version}",
            f"--with-bugurl={BUGURL}", *host.configure]


def avr_libc_args(prefix: Path) -> list[str]:
    return ["--host=avr", f"--prefix={prefix}"]


def build_env(host: Host, native: bool, target_tools: Path) -> dict[str, str]:
    """The environment building binutils and GCC for host. target_tools holds the avr-* programs that
    build the target libraries: the new ones (native) or this machine's (cross)."""
    env = {**os.environ, **host.env, **({} if native else host.cross_env), "LDFLAGS": host.ldflags}
    env["PATH"] = f"{target_tools}{os.pathsep}{env['PATH']}"
    return env


def _target_env(target_tools: Path) -> dict[str, str]:
    """For avr-libc, which avr-gcc compiles: none of the host compiler's settings."""
    env = {k: v for k, v in os.environ.items() if k not in ("CC", "CXX", "CFLAGS", "CXXFLAGS", "LDFLAGS", "CPATH")}
    env["PATH"] = f"{target_tools}{os.pathsep}{env['PATH']}"
    return env


def _make(source: Path, build_dir: Path, args: list[str], env: dict[str, str], jobs: int, install: str) -> None:
    build_dir.mkdir(parents=True)
    print(f"  {source.name}: configure {' '.join(args)}", flush=True)
    for cmd in ([str(source / "configure"), *args], ["make", f"-j{jobs}"], ["make", install]):
        subprocess.run(cmd, cwd=build_dir, env=env, check=True)


def _unpack(archives: dict[str, Path], dest: Path) -> None:
    dest.mkdir(parents=True)
    for c in SOURCES:
        subprocess.run(["tar", "-xf", str(archives[c.name]), "-C", str(dest)], check=True)
    for c in IN_TREE:  # GCC builds GMP, MPFR and MPC itself when they sit in its tree under these names
        (dest / GCC.folder / c.name).symlink_to(f"../{c.folder}")


def build(host: Host, archives: dict[str, Path], work: Path, jobs: int, build_tools: Path | None = None) -> Path:
    """Build and install the toolchain for host into work/<host>/<asset name>; returns that root.
    build_tools: an AVR toolchain root for this machine, needed to cross-build."""
    native = host.native(*this_machine())
    if not native and build_tools is None:
        raise ValueError(f"cross-building {host.name} needs an AVR toolchain for this machine (build its host first)")
    top = work.resolve() / host.name  # configure wants absolute paths
    build_tools = build_tools and build_tools.resolve()
    if top.exists():
        bundle.remove(top)
    _unpack(archives, top / "src")
    root = top / components.name(host.name)
    tools = (root if native else build_tools) / "bin"
    cross_from = None if native else subprocess.run(
        [str(top / "src" / GCC.folder / "config.guess")], capture_output=True, text=True, check=True).stdout.strip()
    env = build_env(host, native, tools)
    _make(top / "src" / BINUTILS.folder, top / "build-binutils", binutils_args(host, root, cross_from), env, jobs,
          "install-strip")
    _make(top / "src" / GCC.folder, top / "build-gcc", gcc_args(host, root, cross_from), env, jobs, "install-strip")
    _make(top / "src" / AVR_LIBC.folder, top / "build-avr-libc", avr_libc_args(root), _target_env(tools), jobs,
          "install")
    strip_target_libraries(root, tools)
    for docs in ("share/info", "share/man"):  # manuals in formats nothing reads from here
        if (root / docs).exists():
            bundle.remove(root / docs)
    return root


def strip_target_libraries(root: Path, tools: Path) -> None:
    """Drop the debug info of the AVR libraries (libgcc, avr-libc and its start files), which are built
    with -g and make up most of the toolchain's size. Firmware linked with them keeps its own."""
    libs = sorted(str(p) for d in ("lib/gcc/avr", "avr/lib") for p in (root / d).rglob("*")
                  if p.suffix in (".a", ".o") and p.is_file())
    subprocess.run([str(tools / "avr-strip"), "--strip-debug", *libs], check=True)


def runtime_packages(host: Host) -> dict[Path, debian.Package]:
    """The Debian package of each runtime file host's programs link statically (none on macOS); run on
    the Debian machine that built them."""
    if not host.runtime_files:
        return {}
    cxx = (host.cross_env if not host.native(*this_machine()) else {}).get("CXX", "g++")
    files = [Path(subprocess.run([*cxx.split(), f"-print-file-name={f}"], capture_output=True, text=True,
                                 check=True).stdout.strip()).resolve() for f in host.runtime_files]
    owned, unowned = debian.provenance(Path("/"), files)
    if unowned:
        raise RuntimeError(f"runtime files no Debian package owns, so their source is unknown: {unowned}")
    return owned


def runtime_sources(packages: dict[Path, debian.Package]) -> set[tuple[str, str]]:
    """(source, version) of every Debian source the runtime files come from, their Built-Using included."""
    return {s for p in packages.values() for s in ((p.source, p.source_version), *p.built_using)}


NOTICE = """\
avr-gcc {gcc} for {host}, built by simee ({repo}) from these unmodified upstream sources:

{rows}

Each component's licence files are in share/doc/<component>/. GMP, MPFR and MPC are linked statically
into the compiler (cc1, cc1plus, lto1). zlib is binutils' and GCC's own bundled copy, except on macOS,
where they use the OS's.

The GitHub release this archive comes from also holds {source}: every archive above and the
scripts that built this toolchain from them.
"""
RUNTIME_NOTICE = """
The build machine's C and C++ runtime (Debian) is linked into the programs statically:

{rows}

Each package's copyright file is in share/doc/<package>/copyright, and {sources} in the same
release holds the exact Debian source of each (with the sources it was built using).
"""


def notice(host: str, runtime: dict[Path, debian.Package]) -> str:
    rows = "\n".join(f"{c.name} {c.version}\t{c.licence}\n\t{c.url}\tsha256 {c.sha256}" for c in SOURCES)
    text = NOTICE.format(gcc=GCC.version, host=host, repo="https://github.com/simee-ai/simee-kicad", rows=rows,
                         source=components.SOURCE_ARCHIVE)
    if runtime:
        lines = sorted(f"{f.name}\t{p.name} {p.version}\t{p.source} {p.source_version}" for f, p in runtime.items())
        text += RUNTIME_NOTICE.format(rows="\n".join(lines), sources=components.RUNTIME_SOURCES)
    return text


def package(host: Host, root: Path, archives: dict[str, Path], out: Path,
            runtime: dict[Path, debian.Package]) -> Path:
    """Add the licences and THIRD-PARTY.txt to root, and archive it into out."""
    components.write_licences(archives, root)
    for pkg in {p.name: p for p in runtime.values()}.values():
        dest = root / "share/doc" / pkg.name / "copyright"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(f"/usr/share/doc/{pkg.name}/copyright").resolve().read_bytes())
    (root / "THIRD-PARTY.txt").write_text(notice(host.name, runtime))
    return bundle.archive(root, out, host.archive)
