"""What the AVR toolchain is built from: upstream source archives pinned by SHA256, and how the release
names its assets. Every release carries these archives and the scripts that built them (GPL-3.0)."""

from dataclasses import dataclass
from pathlib import Path

from kicad_bundle import third_party
from kicad_bundle.fetch import Fetch, cached_file, fetch_url

GNU = "https://ftp.gnu.org/gnu"


@dataclass(frozen=True)
class Source:
    name: str
    version: str
    url: str
    sha256: str
    licence: str

    @property
    def archive(self) -> str:
        return self.url.rsplit("/", 1)[1]

    @property
    def folder(self) -> str:  # the archive's top folder
        return f"{self.name}-{self.version}"


BINUTILS = Source("binutils", "2.47", f"{GNU}/binutils/binutils-2.47.tar.xz",
                  "154ab23b60070e8f27013c22977f1129425d67d1e8acd6e13010e617811e4cff", "GPL-3.0-or-later")
GCC = Source("gcc", "15.3.0", f"{GNU}/gcc/gcc-15.3.0/gcc-15.3.0.tar.xz",
             "fa59c1beef8995f27c4d71c1df227587189315d3e6faff1bb4306e61b0c530eb",
             "GPL-3.0-or-later; libgcc and libstdc++ also under the GCC Runtime Library Exception 3.1")
# Built inside GCC's tree and linked statically into cc1 and cc1plus.
GMP = Source("gmp", "6.3.0", f"{GNU}/gmp/gmp-6.3.0.tar.xz",
             "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898", "LGPL-3.0-or-later")
MPFR = Source("mpfr", "4.2.2", f"{GNU}/mpfr/mpfr-4.2.2.tar.xz",
              "b67ba0383ef7e8a8563734e2e889ef5ec3c3b898a01d00fa0a6869ad81c6ce01", "LGPL-3.0-or-later")
MPC = Source("mpc", "1.3.1", f"{GNU}/mpc/mpc-1.3.1.tar.gz",
             "ab642492f5cf882b74aa0cb730cd410a81edcdbec895183ce930e706c1c759b8", "LGPL-3.0-or-later")
AVR_LIBC = Source("avr-libc", "2.3.2",
                  "https://github.com/avrdudes/avr-libc/releases/download/avr-libc-2_3_2-release/avr-libc-2.3.2.tar.bz2",
                  "92eb253d30cec94f2861a82d40d0b17ad79c0d95fce32b74ffccc26a70afb150", "BSD-3-Clause")
SOURCES = (BINUTILS, GCC, GMP, MPFR, MPC, AVR_LIBC)
IN_TREE = (GMP, MPFR, MPC)

# Releases are avr-gcc-<VERSION>-<n>, numbered apart from kicad-cli's cli-* ones.
VERSION = GCC.version
SOURCE_ARCHIVE = f"avr-gcc-{VERSION}-source.tar"
# The Debian sources of the C/C++ runtime the Linux and Windows binaries link statically.
RUNTIME_SOURCES = f"avr-gcc-{VERSION}-runtime-sources.tar"
SOURCES_CACHE = "avr-gcc-sources"  # under kicad-bundle's download cache
# The repo's files that build a release, beside the Python modules it imports.
BUILD_FILES = ("pyproject.toml", "uv.lock", "avr_toolchain/smoke/blink.c")


def name(host: str) -> str:
    return f"avr-gcc-{VERSION}-{host}"


def fetch(cache: Path, get: Fetch = fetch_url) -> dict[str, Path]:
    """Every source archive by component name, downloaded into cache unless it's there, checked by SHA256."""
    return {c.name: cached_file(c.archive, c.url, c.sha256, cache / SOURCES_CACHE, get) for c in SOURCES}


def source_archive(archives: dict[str, Path], dest: Path) -> Path:
    """dest: <its stem>/<component folder>/<upstream archive> for every component, and simee-build/
    with the scripts that built the release from them."""
    parts = [third_party.Component(c.name, c.version, c.folder, archives[c.name], ((c.archive, archives[c.name]),))
             for c in SOURCES]
    parts.append(third_party.build_scripts("avr_toolchain", BUILD_FILES))
    return third_party.sources_archive(parts, dest)


def write_licences(archives: dict[str, Path], root: Path) -> None:
    """root/share/doc/<component>/<licence files of its source archive>."""
    third_party.write_licences({c.name: third_party.licences(archives[c.name]) for c in SOURCES},
                               root / "share" / "doc")
