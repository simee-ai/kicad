"""Windows: official NSIS installer -> trimmed kicad-cli folder (zip)."""

import os
import shutil
import subprocess
from pathlib import Path

from kicad_bundle import pe, smoke
from kicad_bundle.bundle import archive
from kicad_bundle.closure import closure
from kicad_bundle.release import cached_asset


def _seven_zip() -> str:
    for name in ("7z", "7zz", "7za"):
        if found := shutil.which(name):
            return found
    raise RuntimeError("7-Zip (7z) is needed to unpack the KiCad installer")


def package(version: str, out_dir: Path, cache: Path, work: Path, run_smoke: bool = True,
            arch: str = "x86_64") -> list[Path]:
    installer = cached_asset(version, f"kicad-{version}-{arch}.exe", cache)
    extracted = work / f"windows-{arch}-installer"
    # Everything kicad-cli needs is in the installer's bin/ (0.5 GB vs 4.5 GB for all of it);
    # fall back to a full extraction if a future installer moves it.
    for only in (["bin", "-r"], []):
        if extracted.exists():
            shutil.rmtree(extracted)
        subprocess.run([_seven_zip(), "x", "-y", f"-o{extracted}", str(installer), *only], check=True,
                       stdout=subprocess.DEVNULL)
        cli = next(extracted.rglob("kicad-cli.exe"), None)
        if cli:
            break
    else:
        raise RuntimeError("kicad-cli.exe not found in the installer")
    kiface = next(cli.parent.glob("_eeschema*"), None) or next(extracted.rglob("_eeschema*"))
    keep = closure([cli, kiface], deps=pe.deps, resolve=pe.make_resolver(sorted({cli.parent, kiface.parent})))

    root = work / f"kicad-cli-{version}-windows-{arch}"
    if root.exists():
        shutil.rmtree(root)
    for f in keep:
        dest = root / f.relative_to(extracted)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
    print(f"  {len(keep)} files from the installer")

    if run_smoke:
        if os.name == "nt":
            smoke.check([str(root / cli.relative_to(extracted))])
            print("  smoke test passed")
        else:
            print("  smoke test skipped: needs Windows")
    return [archive(root, out_dir, "zip")]
