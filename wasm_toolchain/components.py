"""What the wasm toolchain is made from: wasi-sdk's own release, one archive per host, pinned by SHA256 (the same
release and checksums simee-core pins in cmake/SimeeWasm.cmake: the two move together), and the licence files of
what the trimmed toolchain keeps, from the upstream sources at the commits the release's VERSION file names (its
archives carry none)."""

from dataclasses import dataclass
from pathlib import Path

from kicad_bundle import cache as kicad_cache
from kicad_bundle.fetch import Fetch, cached_file, fetch_url

VERSION = "34.0"
MAJOR = VERSION.split(".")[0]
DOWNLOADS = f"https://github.com/WebAssembly/wasi-sdk/releases/download/wasi-sdk-{MAJOR}"
# The commits wasi-sdk 34's VERSION file names (llvm 895aa2c896ad, wasi-libc 2e6fb9d8ee0c), and its own tag's.
LLVM_COMMIT = "895aa2c896ada719451be2e3673c83da8ddf1141"
WASI_LIBC_COMMIT = "2e6fb9d8ee0cdf9e431fbcabe8af3115de000a13"
WASI_SDK_COMMIT = "5a0bf653a1a06e1c18867567c5937006d3394a69"
SOURCES_CACHE = kicad_cache.WASI_SDK  # under kicad-bundle's download cache, pruned by cache.prune


@dataclass(frozen=True)
class Download:
    archive: str
    url: str
    sha256: str


def _binary(host: str, sha256: str) -> Download:
    os_, arch = host.split("-")
    archive = f"wasi-sdk-{VERSION}-{arch}-{os_}.tar.gz"  # wasi-sdk names its archives <arch>-<os>
    return Download(archive, f"{DOWNLOADS}/{archive}", sha256)


BINARIES = {
    "macos-arm64": _binary("macos-arm64", "9c59398106b417f8f14913380fdf0097a8cc0ff4af9eb3ce0065a859e88d49e9"),
    "macos-x86_64": _binary("macos-x86_64", "87d27fa8adc68dee59bfbf2e22a6d34ef717c34d6bf1d8af2a56fc929d9ce0eb"),
    "linux-x86_64": _binary("linux-x86_64", "b761e3a0721dbae9c09a0059e5fdb2bf917d1b4a8a7b430fb3b5aafb0984b2c4"),
    "linux-arm64": _binary("linux-arm64", "f7e243dff54d60bcc576e94d6166b69f410f2500ae4a9ceef34315be10e77971"),
    "windows-x86_64": _binary("windows-x86_64", "cccb5c323a9b34f0349a9b09e8804a0a7632c68c3310f4b5f437ed57d7e71d8f"),
}
# The app ships for these; Linux arm64 is a build platform only (simee-core packaging/release/installer.py): its
# archive is for simee-core's own build, never packed into an app.
SHIPPED_IN_APP = ("macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64")


@dataclass(frozen=True)
class Licence:
    path: str  # where it is saved, below share/doc/<component>/
    source: Download  # the file itself, or an archive holding it at <its top folder>/<path>


@dataclass(frozen=True)
class Component:
    name: str  # its folder in share/doc/
    what: str  # what of it the toolchain holds
    licence: str
    licences: tuple[Licence, ...]
    libraries: tuple[str, ...] = ()  # file names (globs) of the shared libraries of it the programs load
    always: bool = True  # shipped in every archive, or only where the programs load one of its libraries


def _raw(repo: str, commit: str, path: str, sha256: str) -> Licence:
    return Licence(path, Download(path, f"https://raw.githubusercontent.com/{repo}/{commit}/{path}", sha256))


_LLVM = ("llvm-project", LLVM_COMMIT)
_LIBC = ("WebAssembly/wasi-libc", WASI_LIBC_COMMIT)
# libedit, which wasi-sdk builds for lldb and links into libLLVM where it builds it (not on Windows, nor macOS x86_64,
# which it cross-compiles); its release, as wasi-sdk 34's cmake/wasi-sdk-toolchain.cmake pins it.
LIBEDIT = Download("libedit-20251016-3.1.tar.gz", "https://thrysoee.dk/editline/libedit-20251016-3.1.tar.gz",
                   "21362b00653bbfc1c71f71a7578da66b5b5203559d43134d2dd7719e313ce041")
# Microsoft's C++ runtime, which wasi-sdk's Windows programs load (MSVCP140, VCRUNTIME140, VCRUNTIME140_1) and which
# Windows doesn't have unless the VC++ Redistributable is installed: shipped next to them as simee ships it beside
# kicad-cli (simee-kicad#9, George), the same files, taken from this repo's kicad-cli release simee-core pins.
MSVC_RUNTIME = Download("kicad-cli-10.0.8-windows-x86_64.zip",
                        "https://github.com/simee-ai/kicad/releases/download/cli-10.0.8-2/"
                        "kicad-cli-10.0.8-windows-x86_64.zip",
                        "523db806883dc87d792463f2cbc86c13b552246cffa56a0449e6e24375bf12c4")
COMPONENTS = (
    Component("llvm-project", "clang, lld (wasm-ld) and LLVM; compiler-rt's builtins, libc++, libc++abi and "
              "libunwind, linked into the modules", "Apache-2.0 WITH LLVM-exception", tuple(
                  _raw(f"llvm/{_LLVM[0]}", _LLVM[1], path, sha) for path, sha in (
                      ("llvm/LICENSE.TXT", "8d85c1057d742e597985c7d4e6320b015a9139385cff4cbae06ffc0ebe89afee"),
                      ("clang/LICENSE.TXT", "ebcd9bbf783a73d05c53ba4d586b8d5813dcdf3bbec50265860ccc885e606f47"),
                      ("lld/LICENSE.TXT", "f7891568956e34643eb6a0db1462db30820d40d7266e2a78063f2fe233ece5a0"),
                      ("compiler-rt/LICENSE.TXT", "1a8f1058753f1ba890de984e48f0242a3a5c29a6a8f2ed9fd813f36985387e8d"),
                      ("libcxx/LICENSE.TXT", "539dd7aed86e8a4f12cbdd0e6c50c189c7d74847e4fecc64ce2c6ee3a01da38b"),
                      ("libcxxabi/LICENSE.TXT", "e2b35be49f7284a45b7baca8fc7b3ab7440e7902392b2528a457816b5bb2a15c"),
                      ("libunwind/LICENSE.TXT", "b5efebcaca80879234098e52d1725e6d9eb8fb96a19fce625d39184b705f7b6d"))),
              libraries=("libLLVM*", "libclang-cpp*")),
    Component("wasi-libc", "the C library and headers of the sysroot, linked into the modules",
              "Apache-2.0 WITH LLVM-exception OR Apache-2.0 OR MIT, with parts under CC0, MIT, BSD-2-Clause and "
              "BSD-3-Clause (its LICENSE)", tuple(
                  _raw(*_LIBC, path, sha) for path, sha in (
                      ("LICENSE", "2711a8b5a5cdfef0e639f96c1aca12ae23d7d64a02d0507f1bdf14d2b27bbc3a"),
                      ("LICENSE-APACHE", "a60eea817514531668d7e00765731449fe14d059d3249e0bc93b36de45f759f2"),
                      ("LICENSE-APACHE-LLVM", "268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5"),
                      ("LICENSE-MIT", "23f18e03dc49df91622fe2a76176497404e46ced8a715d9d2b67a7446571cca3"),
                      ("libc-bottom-half/cloudlibc/LICENSE",
                       "c8b789cf5a746611e6300a0cc7750dbf92b61912a709d04e639245f7290656d0"),
                      ("libc-top-half/musl/COPYRIGHT", "b870108ec5e7790e9f9919064f1b9421d62d5f9b0e6c230c6adf7ea2da62e97b"),
                      ("fts/musl-fts/COPYING", "55af87e4017668f54467a3380e7ebbac5e672d8c763bfe95e6fc882a6fdc4046")))),
    Component("wasi-sdk", "the build of all of the above, and bin/clang.cfg", "Apache-2.0 WITH LLVM-exception",
              (_raw("WebAssembly/wasi-sdk", WASI_SDK_COMMIT, "LICENSE",
                    "268872b9816f90fd8e85db5a28d33f8150ebb8dd016653fb39ef1f94f2686bc5"),)),
    Component("libedit", "command line editing, linked into libLLVM", "BSD-3-Clause",
              (Licence("COPYING", LIBEDIT),), libraries=("libedit*",), always=False),
    Component("microsoft-vc-runtime", "Microsoft's Visual C++ runtime, which the programs load",
              "Visual Studio 2022 Distributable Code (Visual C++ Runtime Files), not open source", (),
              libraries=("vcruntime140*.dll", "msvcp140*.dll"), always=False),
)


def name(host: str) -> str:
    return f"wasi-sdk-{VERSION}-{host}"


def fetch(download: Download, cache: Path, get: Fetch = fetch_url) -> Path:
    """download's file, from cache unless it isn't there, checked by SHA256."""
    return cached_file(Path(download.archive).name, download.url, download.sha256, cache / SOURCES_CACHE, get)
