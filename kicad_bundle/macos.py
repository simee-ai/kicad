"""macOS: official universal DMG -> one trimmed KiCad.app per architecture."""

import platform
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path

from kicad_bundle import macho, smoke
from kicad_bundle.bundle import archive, copy_tree, prune
from kicad_bundle.closure import closure
from kicad_bundle.release import cached_asset

ARCHES = ("arm64", "x86_64")
# kicad-cli loads only the schematic kiface for `sch` commands; eeschema links the rest.
ROOTS = ("MacOS/kicad-cli", "PlugIns/_eeschema.kiface")
TOPS = ("MacOS", "Frameworks", "PlugIns")
DROP = ("SharedSupport", "Applications", "_CodeSignature")  # libraries, sub-apps, stale signature


@contextmanager
def mounted(dmg: Path):
    with tempfile.TemporaryDirectory() as mnt:
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mnt, str(dmg)],
                       check=True, capture_output=True)
        try:
            yield Path(mnt)
        finally:
            subprocess.run(["hdiutil", "detach", "-quiet", mnt], check=False)


def _cli_command(cli: Path, arch: str) -> list[str] | None:
    host = platform.machine()
    if arch == host:
        return [str(cli)]
    if host == "arm64" and arch == "x86_64":
        return ["arch", "-x86_64", str(cli)]  # needs Rosetta
    return None


def package(version: str, out_dir: Path, cache: Path, work: Path, run_smoke: bool = True) -> list[Path]:
    dmg = cached_asset(version, "kicad-unified-universal-*.dmg", cache)
    full = work / "full" / "KiCad.app"
    if full.exists():
        shutil.rmtree(full.parent)
    with mounted(dmg) as mnt:
        app = next(mnt.glob("*/KiCad.app"), None) or next(mnt.glob("KiCad.app"))
        copy_tree(app, full, exclude=tuple(f"Contents/{d}" for d in DROP))
    contents = full / "Contents"

    missing: list[tuple[str, str]] = []
    keep = closure([contents / r for r in ROOTS], deps=macho.deps, resolve=macho.make_resolver(contents),
                   missing=missing)
    if missing:
        raise RuntimeError(f"unresolved libraries: {missing}")

    built = []
    for arch in ARCHES:
        root = work / f"kicad-cli-{version}-macos-{arch}"
        if root.exists():
            shutil.rmtree(root)
        copy_tree(full, root / "KiCad.app")
        app_contents = root / "KiCad.app" / "Contents"
        prune([app_contents / t for t in TOPS], {app_contents / k.relative_to(contents) for k in keep})
        for f in sorted(app_contents.rglob("*")):
            if f.is_file() and not f.is_symlink() and macho.is_macho(f):
                macho.thin(f, arch)
                macho.adhoc_sign(f)
        cli = _cli_command(app_contents / "MacOS" / "kicad-cli", arch)
        if run_smoke:
            if cli is None:
                print(f"  smoke test skipped: can't run {arch} on {platform.machine()}")
            else:
                smoke.check(cli)
                print(f"  smoke test passed ({arch})")
        built.append(archive(root, out_dir, "tar.gz"))
    return built
