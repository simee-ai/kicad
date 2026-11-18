"""wasm-toolchain: package and check the trimmed wasi-sdk simee-core builds its wasm modules with and the app ships
packed, to compile a project's own C++ model (README, "Wasm toolchain").

    wasm-toolchain package --host macos-arm64      # -> dist/wasi-sdk-<version>-macos-arm64.tar.xz, checked here
    wasm-toolchain package                         # every host; each checked where it runs
    wasm-toolchain check dist/wasi-sdk-<version>-windows-x86_64.tar.xz
"""

import argparse
import sys
from pathlib import Path

from arm_toolchain.cli import RUNS_ON
from kicad_bundle.cli import sha256
from kicad_bundle.gnu_build import this_machine
from wasm_toolchain import check, components, package
from wasm_toolchain.components import BINARIES

HOSTS = sorted(BINARIES)


def runs_here(host: str) -> bool:
    """Whether this machine runs host's programs: its own, or Intel macOS ones under Rosetta."""
    return RUNS_ON[host] == this_machine() or (host == "macos-x86_64" and this_machine()[0] == "darwin")


def unpacked_size(root: Path) -> int:
    """Bytes the root takes once unpacked: each file once (hard links share theirs), links nothing."""
    files = {p.stat().st_ino: p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()}
    return sum(files.values())


def _package(args) -> list[Path]:
    built = []
    for host in args.host or HOSTS:
        archive = package.make(host, args.cache, args.work, args.out)
        size = unpacked_size(args.work / components.name(host))
        print(f"  {archive.name}: {archive.stat().st_size / 2**20:.1f} MiB packed, {size / 2**20:.0f} MiB unpacked")
        if args.check and runs_here(host):
            check.check(archive)
        built.append(archive)
    return built


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wasm-toolchain", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("package", help="trim, add the licences to and archive wasi-sdk's release")
    p.add_argument("--host", action="append", choices=HOSTS, help="repeatable (default: every host)")
    p.add_argument("--out", type=Path, default=Path("dist"))
    p.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "kicad-bundle")
    p.add_argument("--work", type=Path, default=Path("work"))
    p.add_argument("--no-check", dest="check", action="store_false",
                   help="skip compiling with the archive (done only for the hosts this machine runs)")
    c = sub.add_parser("check", help="compile, link and run a C++ model with a toolchain archive, unpacked elsewhere, "
                       "with an empty PATH")
    c.add_argument("archive", type=Path)
    args = parser.parse_args(argv)

    if args.command == "check":
        check.check(args.archive)
        return 0
    for path in _package(args):
        print(f"{sha256(path)}  {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
