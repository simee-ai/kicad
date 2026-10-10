"""arm-toolchain: package, check and source the trimmed Arm toolchain simee-core ships (README).

    arm-toolchain package --host macos-arm64       # -> dist/arm-gcc-<release>-macos-arm64.tar.xz, checked here
    arm-toolchain check dist/arm-gcc-<release>-windows-x86_64.zip
    arm-toolchain sources                          # -> dist/<Arm's source snapshot>
"""

import argparse
import platform
import shutil
import sys
from pathlib import Path

from arm_toolchain import check, components, package
from arm_toolchain.components import BINARIES, SOURCE
from kicad_bundle.cli import sha256

# Where each host's programs run: sys.platform and platform.machine().
RUNS_ON = {"macos-arm64": ("darwin", "arm64"), "linux-x86_64": ("linux", "x86_64"),
           "linux-arm64": ("linux", "aarch64"), "windows-x86_64": ("win32", "AMD64")}


def _package(args) -> list[Path]:
    built = []
    for host in args.host or sorted(BINARIES):
        archive = package.make(host, args.cache, args.work, args.out)
        if args.check and RUNS_ON[host] == (sys.platform, platform.machine()):
            check.check(archive)
        built.append(archive)
    return built


def _sources(args) -> list[Path]:
    args.out.mkdir(parents=True, exist_ok=True)
    return [Path(shutil.copy(components.fetch(SOURCE, args.cache), args.out / SOURCE.archive))]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="arm-toolchain", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, default=Path("dist"))
    common.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "kicad-bundle")

    p = sub.add_parser("package", parents=[common], help="trim, add the licences to and archive Arm's toolchain")
    p.add_argument("--host", action="append", choices=sorted(BINARIES), help="repeatable (default: every host)")
    p.add_argument("--work", type=Path, default=Path("work"))
    p.add_argument("--no-check", dest="check", action="store_false",
                   help="skip compiling with the archive (done only for this machine's host)")

    c = sub.add_parser("check", help="compile for the RP2040 with a toolchain archive, unpacked elsewhere, "
                       "with an empty PATH")
    c.add_argument("archive", type=Path)

    sub.add_parser("sources", parents=[common], help="the release's source asset: Arm's source snapshot")
    args = parser.parse_args(argv)

    if args.command == "check":
        check.check(args.archive)
        return 0
    for path in (_package if args.command == "package" else _sources)(args):
        print(f"{sha256(path)}  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
