import argparse
import hashlib
import sys
from pathlib import Path

from kicad_bundle import cache, linux, macos, windows

PACKAGERS = {"linux": linux.package, "macos": macos.package, "windows": windows.package}
# Platforms that can build KiCad's own files from a simee-kicad branch (Windows: #13).
SIMEE_BUILDS = {"linux", "macos"}


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
    parser.add_argument("--simee-ref", help="build KiCad's own files from this simee-kicad branch, e.g. simee/10.0.6")
    args = parser.parse_args(argv)
    if args.simee_ref and args.platform not in SIMEE_BUILDS:
        parser.error(f"--simee-ref isn't supported on {args.platform} yet")

    simee = {"simee_ref": args.simee_ref} if args.simee_ref else {}
    built = PACKAGERS[args.platform](args.kicad_version, args.out, args.cache, args.work, not args.no_smoke, **simee)
    for path in built:
        print(f"{sha256(path)}  {path.name}  ({path.stat().st_size / 1e6:.0f} MB)")
    cache.prune(args.cache, built=args.kicad_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
