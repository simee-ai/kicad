"""Build the Arm toolchain for macOS x86_64, which Arm stopped publishing after 14.2.rel1, so every host
gets the same release.

The target side doesn't depend on the host: the headers, newlib, libstdc++, libgcc and the specs (what
package.KEEP keeps outside the host programs) come unchanged from Arm's macOS arm64 archive. Only the host
programs are built, for x86_64, from Arm's source snapshot: binutils, and GCC's all-gcc (the drivers, cc1,
cc1plus, lto1, collect2, lto-wrapper) and its LTO plugin, with GMP, MPFR, MPC and isl from the snapshot in
GCC's tree, linked statically. They are configured as Arm configured its macOS arm64 build (the manifest
in its archive), so the driver picks the same multilibs and searches the same paths, against Arm's own
sysroot. On an arm64 Mac they are cross-built with `clang -arch x86_64`, with Arm's arm64 toolchain as the
compiler for this machine that GCC's build runs, and they run under Rosetta.
"""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

from arm_toolchain import components, package
from arm_toolchain.components import BINARIES, BUILD_FILES, BUILD_SCRIPTS, MULTILIB_LIST, RELEASE, SOURCE
from kicad_bundle import bundle, gnu_build, third_party
from kicad_bundle.gnu_build import BUGURL, MACOS_CONFIGURE, MACOS_ENV, MACOS_MIN, macos_compilers, this_machine

HOST = "macos-x86_64"
TRIPLE = "x86_64-apple-darwin"
TARGET_FROM = "macos-arm64"  # the Arm archive the target side comes from
PKGVERSION = f"Arm GNU Toolchain {RELEASE}, built by simee for macOS x86_64"
# What runs on the host, below the toolchain root: the drivers and binutils, the compilers, and the
# binutils GCC runs (as, ld, ...).
HOST_PROGRAMS = ("bin", "libexec", "arm-none-eabi/bin")
SNAPSHOT_FOLDERS = ("binutils-gdb", "gcc", "gmp", "mpfr", "mpc", "isl")
IN_TREE = ("gmp", "mpfr", "mpc", "isl")  # GCC builds them itself when they sit in its tree
# Arm's options that name its build machine's folders, or that are set otherwise here: they are replaced.
REPLACED = ("--prefix", "--with-sysroot", "--with-bugurl", "--with-pkgversion", "--enable-languages",
            "--with-gmp", "--with-mpfr", "--with-mpc", "--with-isl", "--build", "--host")
# Arm's macOS programs link only the OS's libraries (libSystem, libc++, libiconv), so no zstd; and the OS's
# libz, as binutils' and GCC's bundled zlib don't compile against current macOS SDKs (MACOS_CONFIGURE).
_OURS = ("--without-zstd", *MACOS_CONFIGURE)


def host_program(rel: str) -> bool:
    return any(rel == p or rel.startswith(f"{p}/") for p in HOST_PROGRAMS)


def _files(root: Path) -> list[str]:
    """Every file and symlink below root, as paths relative to it."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_symlink() or not p.is_dir())


def arm_programs(rels) -> list[str]:
    """The host programs among the paths of Arm's trimmed toolchain: what the build must make too."""
    return sorted(rel for rel in rels if host_program(rel))


def arm_options(manifest: str, step: str) -> list[str]:
    """Arm's configure options for step (binutils, gcc2: the final GCC) from its archive's manifest."""
    for line in manifest.splitlines():
        if line.startswith(f"{step}_configure="):
            return shlex.split(line.partition("=")[2])
    raise RuntimeError(f"Arm's manifest has no {step}_configure")


def _configure(arm: list[str], prefix: Path, cross_from: str | None, *ours: str) -> list[str]:
    return [*(o for o in arm if o.partition("=")[0] not in REPLACED), f"--prefix={prefix}",
            f"--with-sysroot={prefix}/arm-none-eabi", f"--with-bugurl={BUGURL}",
            *gnu_build.host_args(TRIPLE, cross_from), *_OURS, *ours]


def binutils_args(arm: list[str], prefix: Path, cross_from: str | None) -> list[str]:
    """arm: Arm's options (arm_options); cross_from: this machine's triple when cross-building."""
    return _configure(arm, prefix, cross_from, "--disable-werror", "--disable-sim")


def gcc_args(arm: list[str], prefix: Path, cross_from: str | None) -> list[str]:
    # C and C++ only: Arm's fortran compiler (f951) isn't kept.
    return _configure(arm, prefix, cross_from, "--enable-languages=c,c++", f"--with-pkgversion={PKGVERSION}")


# Arm's snapshot is a git checkout, without the generated .info manuals, which aren't kept anyway: none
# are made. (configure swaps an environment's MAKEINFO=true for `missing makeinfo`, so it goes to make.)
MAKE_VARS = ("MAKEINFO=true",)


def build_env(environ: dict[str, str], build_tools: Path | None) -> dict[str, str]:
    """build_tools: when cross-building, Arm's toolchain for this machine, which GCC's build runs."""
    env = {**environ, **MACOS_ENV, **macos_compilers("x86_64")}
    if build_tools:
        env["PATH"] = f"{build_tools / 'bin'}{os.pathsep}{env['PATH']}"
    return env


def unpack_snapshot(snapshot: Path, dest: Path) -> Path:
    """The SNAPSHOT_FOLDERS of Arm's source snapshot in dest, with GCC's libraries linked into its tree."""
    dest.mkdir(parents=True)
    subprocess.run(["tar", "-xf", str(snapshot), "-C", str(dest), *(f"./{f}" for f in SNAPSHOT_FOLDERS)],
                   check=True)
    for lib in IN_TREE:
        (dest / "gcc" / lib).symlink_to(f"../{lib}")
    return dest


def _copy(src: Path, dest: Path, rels, link: bool = False) -> Path:
    """Each of rels from src to dest: hard-linked (link) or copied, symlinks as symlinks."""
    for rel in rels:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        if link and not (src / rel).is_symlink():
            os.link(src / rel, dest / rel)
        else:
            shutil.copy2(src / rel, dest / rel, follow_symlinks=False)
    return dest


def sysroot_copy(arm: Path, stage: Path) -> Path:
    """The install prefix to build in, holding a copy of the target side of Arm's toolchain (arm): the
    sysroot GCC is built against, as Arm's was. A copy, as the install writes over some of it."""
    return _copy(arm, stage, [rel for rel in _files(arm) if not host_program(rel)])


def assemble(arm: Path, built: Path, root: Path) -> Path:
    """root: the target side of Arm's toolchain (arm), linked so its hard links stay, and from the install
    prefix built, each of Arm's host programs; refuses when built lacks one. Files GCC installs on the
    target side give way to Arm's."""
    rels = _files(arm)
    programs = arm_programs(rels)
    if missing := [p for p in programs if not (built / p).exists()]:
        raise RuntimeError(f"the build made none of Arm's {', '.join(missing)}")
    _copy(arm, root, [rel for rel in rels if not host_program(rel)], link=True)
    return _copy(built, root, programs)


def build(arm_archive: Path, snapshot: Path, work: Path, jobs: int) -> Path:
    """Build and assemble the toolchain into work/<host>/<asset name>; returns that root. The install
    prefix the programs were built for is deleted, so nothing can still reach into it."""
    top = work.resolve() / HOST  # configure wants absolute paths
    if top.exists():
        bundle.remove(top)
    arm = package.extract(arm_archive, top, components.name(TARGET_FROM))  # Arm's toolchain, trimmed
    stage = sysroot_copy(arm, top / "stage" / components.name(HOST))
    [manifest] = arm.glob("*manifest.txt")
    src = unpack_snapshot(snapshot, top / "src")
    native = this_machine() == ("darwin", "x86_64")
    cross_from = None if native else gnu_build.config_guess(src / "gcc")
    env = build_env(dict(os.environ), None if native else arm)
    options = manifest.read_text()
    binutils = binutils_args(arm_options(options, "binutils"), stage, cross_from)
    gnu_build.make(src / "binutils-gdb", top / "build-binutils", binutils, env, jobs, ("install-strip",),
                   make_vars=MAKE_VARS)
    gcc = gcc_args(arm_options(options, "gcc2"), stage, cross_from)
    gnu_build.make(src / "gcc", top / "build-gcc", gcc, env, jobs, ("install-strip-gcc", "install-strip-lto-plugin"),
                   ("all-gcc", "all-lto-plugin"), MAKE_VARS)
    root = assemble(arm, stage, top / components.name(HOST))
    bundle.remove(top / "stage")
    return root


NOTICE = """\
Arm GNU Toolchain {release} for arm-none-eabi, {host}, which Arm doesn't build after 14.2.rel1, so
simee ({repo}) assembles it from two of Arm's downloads for the release:

- the host programs (the C and C++ drivers, binutils, cc1, cc1plus, collect2 and LTO) built for x86_64,
  for macOS {macos} or later, from Arm's source snapshot, unmodified,

\t{source_url}
\tsha256 {source_sha256}

  configured as Arm configured its macOS arm64 build (the manifest file next to this one is Arm's record
  of that), with GMP, MPFR, MPC and isl from the snapshot linked in statically. They link only macOS's
  own libraries (libSystem, libc++, libiconv, libz);

- the target files (the headers, and newlib, libstdc++ and libgcc for the multilibs {multilibs}),
  Arm's own macOS arm64 build of them, unmodified, from

\t{url}
\tsha256 {sha256}

"""
SOURCES = """
The GitHub release this archive comes from also holds
{source}, Arm's source snapshot for this release, which the programs and
libraries above are built from, and {scripts}, the scripts that built the programs from it.
"""


def notice() -> str:
    b = BINARIES[TARGET_FROM]
    return (NOTICE.format(release=RELEASE, host=HOST, repo=gnu_build.REPO, macos=MACOS_MIN, source_url=SOURCE.url,
                          source_sha256=SOURCE.sha256, multilibs=MULTILIB_LIST, url=b.url, sha256=b.sha256)
            + package.COMPONENTS + SOURCES.format(source=SOURCE.archive, scripts=BUILD_SCRIPTS))


def build_scripts(out: Path) -> Path:
    """out/BUILD_SCRIPTS: simee-build/, the scripts that build the programs."""
    return third_party.sources_archive([third_party.build_scripts("arm_toolchain", BUILD_FILES)], out / BUILD_SCRIPTS)


def make(cache: Path, work: Path, out: Path, jobs: int) -> Path:
    """Download (or reuse) Arm's macOS arm64 archive and the source snapshot, build and package."""
    snapshot = components.fetch(SOURCE, cache)
    root = build(components.fetch(BINARIES[TARGET_FROM], cache), snapshot, work, jobs)
    return package.package(root, snapshot, out, notice(), "tar.xz")
