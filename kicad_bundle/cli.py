import argparse
import hashlib
import sys
from pathlib import Path

from kicad_bundle import macos, windows

PACKAGERS = {"macos": macos.package, "windows": windows.package}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kicad-bundle", description=__doc__)
    parser.add_argument("--kicad-version", required=True, help="official KiCad release, e.g. 10.0.6")
    parser.add_argument("--platform", required=True, choices=sorted(PACKAGERS))
    parser.add_argument("--out", type=Path, default=Path("dist"))
    parser.add_argument("--work", type=Path, default=Path("work"))
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "kicad-bundle")
    parser.add_argument("--no-smoke", action="store_true", help="skip running the packaged kicad-cli")
    args = parser.parse_args(argv)

    built = PACKAGERS[args.platform](args.kicad_version, args.out, args.cache, args.work, not args.no_smoke)
    for path in built:
        print(f"{sha256(path)}  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
