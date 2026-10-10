"""What the Arm toolchain is made from: Arm's own GNU toolchain release for arm-none-eabi, one binary
archive per host and the source snapshot they are built from, all pinned by SHA256 (the ones Arm
publishes next to each download), and how the release names its assets."""

from dataclasses import dataclass
from pathlib import Path

from kicad_bundle.fetch import Fetch, cached_file, fetch_url

RELEASE = "15.2.rel1"
GCC_VERSION = "15.2.1"
DOWNLOADS = f"https://developer.arm.com/-/media/Files/downloads/gnu/{RELEASE}"


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
    return Binary(archive, f"{DOWNLOADS}/binrel/{archive}", sha256, arm_host)


# Arm builds no macOS x86_64 toolchain after 14.2.rel1: simee builds that one (arm_toolchain/build.py).
BINARIES = {
    "macos-arm64": _binary("darwin-arm64", "tar.xz", "1938a84b7105c192e3fb4fa5e893ba25f425f7ddab40515ae608cd40f68669a8"),
    "linux-x86_64": _binary("x86_64", "tar.xz", "597893282ac8c6ab1a4073977f2362990184599643b4c5ee34870a8215783a16"),
    "linux-arm64": _binary("aarch64", "tar.xz", "d061559d814b205ed30c5b7c577c03317ec447ca51cd5a159d26b12a5bbeb20c"),
    "windows-x86_64": _binary("mingw-w64-x86_64", "zip",
                              "7936cac895611023ffb22a64b8e426098c7104cb689778c1894572ca840b9ece"),
}
_SOURCE_ARCHIVE = f"arm-gnu-toolchain-src-snapshot-{RELEASE}.tar.xz"
# Published unchanged as the release's source asset (GPL).
SOURCE = Download(_SOURCE_ARCHIVE, f"{DOWNLOADS}/srcrel/{_SOURCE_ARCHIVE}",
                  "06f4e5792b566a771c300ac6f46c3422e23e34f4e3a5d44a8cb5594ead6fb82e")

# The snapshot's folders whose code the kept files hold: binutils, GCC (with libgcc and libstdc++),
# newlib (libc, libm, libnosys), and GMP, MPFR, MPC, isl and libiconv linked into the programs. The
# others (gdb's binutils-gdb--gdb, glibc, linux, ncurses, libexpat) only go into files that are dropped.
SHIPPED = ("binutils-gdb", "gcc", "newlib-cygwin", "gmp", "mpfr", "mpc", "isl", "libiconv")

# The multilibs kept, as GCC names them (-print-multi-directory): the RP2040's Cortex-M0+ (no FPU). The
# RP2350 (Cortex-M33) will add thumb/v8-m.main+fp/softfp (simee-core#111).
MULTILIBS = ("thumb/v6-m/nofp",)
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
