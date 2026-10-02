"""Mach-O helpers: dependency listing, bundle-relative resolution, thinning, ad hoc signing."""

import subprocess
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
