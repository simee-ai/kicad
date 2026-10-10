"""What the AVR and Arm toolchain builds share: configuring, making and installing a GNU component in a
build folder of its own, cross-built for another host when needed, and the macOS hosts' compilers."""

import platform
import subprocess
import sys
from pathlib import Path

REPO = "https://github.com/simee-ai/simee-kicad"
BUGURL = f"{REPO}/issues"  # for the toolchains simee builds
MACOS_MIN = "11.0"  # the first macOS on Apple silicon
MACOS_ENV = {"MACOSX_DEPLOYMENT_TARGET": MACOS_MIN}
# The OS's own libz: GCC's bundled zlib doesn't compile against current macOS SDKs (its fdopen macro).
MACOS_CONFIGURE = ("--with-system-zlib",)
# Xcode's tools handle every architecture, and there are no <triple>-ar etc. for configure to find.
_MACOS_TOOLS = {"AR": "ar", "RANLIB": "ranlib", "NM": "nm", "STRIP": "strip"}


def macos_compilers(arch: str) -> dict[str, str]:
    """The environment that compiles for macOS on arch (arm64, x86_64) on either kind of Mac."""
    return {**_MACOS_TOOLS, "CC": f"clang -arch {arch}", "CXX": f"clang++ -arch {arch}"}


def this_machine() -> tuple[str, str]:
    return sys.platform, platform.machine()


def host_args(triple: str, cross_from: str | None) -> list[str]:
    """configure's options building for host triple; cross_from: this machine's triple when cross-building."""
    return [f"--build={cross_from}", f"--host={triple}"] if cross_from else []


def config_guess(source: Path) -> str:
    """This machine's triple, as the GNU source tree at source names it."""
    return subprocess.run([str(source / "config.guess")], capture_output=True, text=True, check=True).stdout.strip()


def make(source: Path, build_dir: Path, args: list[str], env: dict[str, str], jobs: int,
         install: tuple[str, ...] = ("install",), targets: tuple[str, ...] = (),
         make_vars: tuple[str, ...] = ()) -> None:
    """Configure source in build_dir with args, make targets (default: all) and the install targets,
    with make_vars (NAME=value, which every sub-make inherits) on both make command lines."""
    build_dir.mkdir(parents=True)
    print(f"  {source.name}: configure {' '.join(args)}", flush=True)
    for cmd in ([str(source / "configure"), *args], ["make", f"-j{jobs}", *make_vars, *targets],
                ["make", *make_vars, *install]):
        subprocess.run(cmd, cwd=build_dir, env=env, check=True)
