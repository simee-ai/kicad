"""What the Arm toolchain is made from: Arm's own GNU toolchain release for arm-none-eabi, one binary
archive per host and the source snapshot they are built from, all pinned by SHA256 (the ones Arm
publishes next to each download), and how the release names its assets."""

from dataclasses import dataclass
from pathlib import Path

from kicad_bundle.fetch import Fetch, cached_file, fetch_url

RELEASE = "15.3.rel2"
GCC_VERSION = "15.3.1"
# From 15.3, Arm publishes each release's downloads as GitLab generic packages, linked from the README of
# its releases/<release> branch, no longer under developer.arm.com's /-/media/Files/downloads/gnu/.
DOWNLOADS = ("https://gitlab.arm.com/api/v4/projects/tooling%2Fgnu-toolchains-for-arm/packages/generic/"
             f"gnu-toolchain/{RELEASE}")


@dataclass(frozen=True)
class Download:
    archive: str
    url: str
    sha256: str


@dataclass(frozen=True)
class Binary(Download):
    arm_host: str  # Arm's name for the host, in its archive names

    @property
    def format(self) -> str:
        return "zip" if self.archive.endswith(".zip") else "tar.xz"


def _binary(arm_host: str, ext: str, sha256: str) -> Binary:
    archive = f"arm-gnu-toolchain-{RELEASE}-{arm_host}-arm-none-eabi.{ext}"
    return Binary(archive, f"{DOWNLOADS}/{archive}", sha256, arm_host)


# Arm builds no macOS x86_64 toolchain after 14.2.rel1: simee builds that one (arm_toolchain/build.py).
BINARIES = {
    "macos-arm64": _binary("darwin-arm64", "tar.xz", "b6ccf805aa4fb8c50406256f99cf802385894b86c0cb6c0185dc0e2b5f8288b9"),
    "linux-x86_64": _binary("x86_64", "tar.xz", "0662b2f01e0cd8b951fd110911d339c35b05998c68a12b3530bbbb4bf6c7874e"),
    "linux-arm64": _binary("aarch64", "tar.xz", "9fc9e6baedf2b6779bd39ce4cc458d3529e200bc92bebe28be18f8942a456ad8"),
    "windows-x86_64": _binary("mingw-w64-x86_64", "zip",
                              "062888d4f8a213f930e367189804910e5215c4e623e5063e64494c302f94b889"),
}
_SOURCE_ARCHIVE = f"arm-gnu-toolchain-src-snapshot-{RELEASE}.tar.xz"
# Published unchanged as the release's source asset (GPL).
SOURCE = Download(_SOURCE_ARCHIVE, f"{DOWNLOADS}/{_SOURCE_ARCHIVE}",
                  "fec2f0008bbe6be5016328f9f95ec750db00499653d775d934b2330f717c274c")

# The snapshot's folders whose code the kept files hold: binutils, GCC (with libgcc and libstdc++),
# newlib (libc, libm, libnosys), and GMP, MPFR, MPC, isl, libiconv and (from 15.3, in cc1 and lto1) zstd
# linked into the programs. The others (gdb's binutils-gdb--gdb, glibc, linux, ncurses, libexpat) only go
# into files that are dropped.
SHIPPED = ("binutils-gdb", "gcc", "newlib-cygwin", "gmp", "mpfr", "mpc", "isl", "libiconv", "zstd")


@dataclass(frozen=True)
class Target:
    chip: str
    flags: tuple[str, ...]  # pico-sdk's for the chip, which make the driver pick the multilib


# The multilibs kept, as GCC names them (-print-multi-directory), with the chip each is for: the RP2040's
# Cortex-M0+ (no FPU), and the RP2350's Cortex-M33 in Arm mode (its FPU with the soft-float calling
# convention, as pico-sdk builds it; simee-core#111).
MULTILIBS = {
    "thumb/v6-m/nofp": Target("RP2040's Cortex-M0+", ("-mcpu=cortex-m0plus", "-mthumb", "-mfloat-abi=soft")),
    "thumb/v8-m.main+fp/softfp": Target("RP2350's Cortex-M33", (
        "-mcpu=cortex-m33", "-mthumb", "-march=armv8-m.main+fp+dsp", "-mfloat-abi=softfp", "-mcmse")),
}
CHIPS = " and the ".join(t.chip for t in MULTILIBS.values())  # "for the {CHIPS}"
MULTILIB_LIST = " and ".join(MULTILIBS)
SOURCES_CACHE = "arm-gcc-sources"  # under kicad-bundle's download cache
# The macOS x86_64 programs are built by simee (arm_toolchain/build.py), so the release also carries the
# scripts that build them, beside Arm's snapshot they are built from.
BUILD_SCRIPTS = f"arm-gcc-{RELEASE}-build-scripts.tar"
BUILD_FILES = ("pyproject.toml", "uv.lock")


def name(host: str) -> str:
    return f"arm-gcc-{RELEASE}-{host}"


def fetch(download: Download, cache: Path, get: Fetch = fetch_url) -> Path:
    """download's archive, from cache unless it isn't there, checked by SHA256."""
    return cached_file(download.archive, download.url, download.sha256, cache / SOURCES_CACHE, get)
