"""arm-toolchain: package, check and source the trimmed Arm toolchain simee-core ships (README).

    arm-toolchain package --host macos-arm64       # -> dist/arm-gcc-<release>-macos-arm64.tar.xz, checked here
    arm-toolchain package --host macos-x86_64      # built from Arm's sources on a Mac, checked (Rosetta on arm64)
    arm-toolchain check dist/arm-gcc-<release>-windows-x86_64.zip
    arm-toolchain sources                          # -> dist/<Arm's source snapshot>, dist/<build scripts>
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

from arm_toolchain import build, check, components, package
from arm_toolchain.components import BINARIES, SOURCE
from kicad_bundle.cli import sha256
from kicad_bundle.gnu_build import this_machine

# Where each host's programs run: sys.platform and platform.machine().
RUNS_ON = {"macos-arm64": ("darwin", "arm64"), "macos-x86_64": ("darwin", "x86_64"),
           "linux-x86_64": ("linux", "x86_64"), "linux-arm64": ("linux", "aarch64"),
           "windows-x86_64": ("win32", "AMD64")}
HOSTS = sorted(RUNS_ON)


def runs_here(host: str) -> bool:
    """Whether this machine runs host's programs: its own, or Intel macOS ones under Rosetta."""
    return RUNS_ON[host] == this_machine() or (host == build.HOST and this_machine()[0] == "darwin")


def _package(args) -> list[Path]:
    built = []
    for host in args.host or sorted(BINARIES):
        if host == build.HOST:
            jobs = int(os.environ.get("CMAKE_BUILD_PARALLEL_LEVEL") or os.cpu_count() or 1)
            archive = build.make(args.cache, args.work, args.out, jobs)
        else:
            archive = package.make(host, args.cache, args.work, args.out)
        if args.check and runs_here(host):
            check.check(archive)
        built.append(archive)
    return built


def _sources(args) -> list[Path]:
    args.out.mkdir(parents=True, exist_ok=True)
    return [Path(shutil.copy(components.fetch(SOURCE, args.cache), args.out / SOURCE.archive)),
            build.build_scripts(args.out)]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="arm-toolchain", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, default=Path("dist"))
    common.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "kicad-bundle")

    p = sub.add_parser("package", parents=[common], help="trim, add the licences to and archive Arm's toolchain")
    p.add_argument("--host", action="append", choices=HOSTS,
                   help=f"repeatable (default: every host but {build.HOST}, which is built on a Mac)")
    p.add_argument("--work", type=Path, default=Path("work"))
    p.add_argument("--no-check", dest="check", action="store_false",
                   help="skip compiling with the archive (done only for this machine's host)")

    c = sub.add_parser("check", help="compile for the RP2040 and RP2350 with a toolchain archive, unpacked elsewhere, "
                       "with an empty PATH")
    c.add_argument("archive", type=Path)

    sub.add_parser("sources", parents=[common], help="the release's source assets: Arm's source snapshot, and "
                   f"the scripts that build {build.HOST}'s programs from it")
    args = parser.parse_args(argv)

    if args.command == "check":
        check.check(args.archive)
        return 0
    for path in (_package if args.command == "package" else _sources)(args):
        print(f"{sha256(path)}  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
