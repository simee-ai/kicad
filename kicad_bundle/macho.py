"""Mach-O helpers: dependency listing, bundle-relative resolution, thinning, ad hoc signing."""

import struct
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

SYSTEM_PREFIXES = ("/System/", "/usr/lib/")


def parse_otool(output: str) -> list[str]:
    """Install names from `otool -L` output (the first line names the file itself)."""
    return [line.strip().split(" (")[0] for line in output.splitlines()[1:] if line.strip()]


def deps(binary: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(binary)], capture_output=True, text=True, check=True).stdout
    return [name for name in parse_otool(out) if Path(name) != binary]


def make_resolver(contents: Path):
    """Resolve install names against a KiCad.app/Contents directory; system libraries -> None."""
    frameworks = contents / "Frameworks"

    def resolve(name: str, binary: Path) -> Path | None:
        if name.startswith(SYSTEM_PREFIXES):
            return None
        if name.startswith("@rpath/"):
            rel = name[len("@rpath/"):]
            direct = frameworks / rel
            return direct if direct.exists() else frameworks / "Python.framework" / rel
        if name.startswith("@executable_path/"):
            return (contents / "MacOS" / name[len("@executable_path/"):]).resolve(strict=False)
        if name.startswith("@loader_path/"):
            return binary.parent / name[len("@loader_path/"):]
        return Path(name)

    return resolve


@dataclass(frozen=True)
class Slice:
    uuid: str  # LC_UUID: set by the linker, kept by install_name_tool, lipo and codesign
    minos: tuple[int, int]  # the macOS a binary was built for; Homebrew builds bottles for the OS it runs on


ARCHS = {0x0100000C: "arm64", 0x01000007: "x86_64"}
LC_UUID, LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX = 0x1B, 0x32, 0x24


def _slice(data: bytes, offset: int) -> tuple[str, Slice] | None:
    magic, cpu, _, _, ncmds = struct.unpack_from("<IiiII", data, offset)
    if magic != 0xFEEDFACF or cpu not in ARCHS:
        return None
    found, minos, at = "", (0, 0), offset + 32
    for _ in range(ncmds):
        cmd, size = struct.unpack_from("<II", data, at)
        if cmd == LC_UUID:
            found = str(uuid.UUID(bytes=data[at + 8:at + 24])).upper()
        elif cmd in (LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX):
            version = struct.unpack_from("<I", data, at + (12 if cmd == LC_BUILD_VERSION else 8))[0]
            minos = (version >> 16, (version >> 8) & 0xFF)
        at += size
    return ARCHS[cpu], Slice(found, minos)


def slices(data: bytes) -> dict[str, Slice]:
    """Each architecture of a 64-bit Mach-O image (thin or fat) -> its UUID and minimum macOS."""
    if data[:4] == b"\xca\xfe\xba\xbe":
        count = struct.unpack_from(">I", data, 4)[0]
        offsets = [struct.unpack_from(">iiI", data, 8 + 20 * i)[2] for i in range(count)]
    else:
        offsets = [0]
    return dict(s for o in offsets if len(data) >= o + 32 and (s := _slice(data, o)))


def is_macho(path: Path) -> bool:
    with path.open("rb") as f:
        magic = f.read(4)
    return magic in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")


def thin(path: Path, arch: str) -> None:
    archs = subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True, check=True).stdout.split()
    if len(archs) > 1:
        tmp = path.with_name(path.name + ".thin")
        subprocess.run(["lipo", "-thin", arch, str(path), "-output", str(tmp)], check=True)
        tmp.replace(path)


def adhoc_sign(path: Path) -> None:
    # Deleting files or thinning invalidates the original signatures; dyld refuses those.
    subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True)
