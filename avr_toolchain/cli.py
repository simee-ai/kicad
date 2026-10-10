"""avr-toolchain: build, check and source the relocatable AVR toolchain simee-core ships (README).

    avr-toolchain build --host macos-arm64          # -> dist/avr-gcc-<v>-macos-arm64.tar.gz, checked
    avr-toolchain build --host macos-x86_64         # cross-built with the macos-arm64 one in --work
    avr-toolchain check dist/avr-gcc-<v>-windows-x86_64.zip
    avr-toolchain sources --runtime-of linux-x86_64 --runtime-of windows-x86_64
"""

import argparse
import os
import sys
from pathlib import Path

from avr_toolchain import build, check, components
from kicad_bundle import cache as kicad_cache
from kicad_bundle import debian
from kicad_bundle.cli import sha256


def _build(args) -> list[Path]:
    host = build.HOSTS[args.host]
    archives = components.fetch(args.cache)
    tools = args.build_tools
    if tools is None and not host.native(*build.this_machine()) and (native := build.native_host()):
        tools = args.work / native.name / components.name(native.name)
    jobs = int(os.environ.get("CMAKE_BUILD_PARALLEL_LEVEL") or os.cpu_count() or 1)
    root = build.build(host, archives, args.work, jobs, tools)
    archive = build.package(host, root, archives, args.out, build.runtime_packages(host))
    if args.check and build.runnable(host):
        moved = root.with_name(root.name + ".moved")  # nothing may still reach into the build's own prefix
        root.rename(moved)
        try:
            check.check(archive)
        finally:
            moved.rename(root)
    return [archive]


def _sources(args) -> list[Path]:
    archives = components.fetch(args.cache)
    built = [components.source_archive(archives, args.out / components.SOURCE_ARCHIVE)]
    if args.runtime_of:
        wanted = set().union(*(build.runtime_sources(build.runtime_packages(build.HOSTS[h])) for h in args.runtime_of))
        built.append(debian.sources_archive(wanted, args.out / components.RUNTIME_SOURCES,
                                            args.cache / kicad_cache.DEBIAN_SOURCES))
    return built


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="avr-toolchain", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--out", type=Path, default=Path("dist"))
    common.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "kicad-bundle")

    b = sub.add_parser("build", parents=[common], help="build, archive and check the toolchain for a host")
    b.add_argument("--host", required=True, choices=sorted(build.HOSTS))
    b.add_argument("--work", type=Path, default=Path("work"))
    b.add_argument("--build-tools", type=Path, help="an AVR toolchain root for this machine, to cross-build "
                   "(default: this machine's host built in --work)")
    b.add_argument("--no-check", dest="check", action="store_false", help="skip compiling blink with the archive")

    c = sub.add_parser("check", help="compile blink with a toolchain archive, unpacked elsewhere, with an empty PATH")
    c.add_argument("archive", type=Path)

    s = sub.add_parser("sources", parents=[common], help="the release's source archives")
    s.add_argument("--runtime-of", action="append", choices=sorted(build.HOSTS), default=[],
                   help="also the Debian sources of the runtime this host's build links statically (on that machine)")
    args = parser.parse_args(argv)

    if args.command == "check":
        check.check(args.archive)
        return 0
    for path in (_build if args.command == "build" else _sources)(args):
        print(f"{sha256(path)}  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
