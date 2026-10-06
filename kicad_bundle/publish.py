"""Release bookkeeping for the package workflow: tag numbering, checksums and notes.

    python -m kicad_bundle.publish --kicad-version 10.0.6 --dist dist --tags tags.txt --run-url URL

writes dist/SHA256SUMS and notes.md and prints the release tag.
"""

import argparse
import hashlib
import re
from pathlib import Path


def next_tag(version: str, existing: list[str]) -> str:
    """cli-<kicad version>-<n>: n counts our builds of that KiCad release."""
    builds = [int(m.group(1)) for t in existing if (m := re.fullmatch(rf"cli-{re.escape(version)}-(\d+)", t))]
    return f"cli-{version}-{max(builds, default=0) + 1}"


def sha256sums(dist: Path) -> str:
    lines = []
    for path in sorted(p for p in dist.iterdir() if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    return "\n".join(lines) + "\n"


def release_notes(version: str, run_url: str, assets: list[str]) -> str:
    listed = "\n".join(f"- `{a}`" for a in sorted(assets))
    return f"""Trimmed `kicad-cli` from the official, unmodified KiCad {version} release: just what
`kicad-cli sch ...` needs (the schematic module and its shared libraries), re-signed ad hoc on macOS.
Unpack and run `kicad-cli` (macOS: `KiCad.app/Contents/MacOS/kicad-cli`; Windows: `bin\\kicad-cli.exe`;
Linux x86_64: `bin/kicad-cli`, which needs glibc 2.39 or newer, e.g. Ubuntu 24.04 or Debian 13).
Set `KICAD_CONFIG_HOME`, `KICAD_DOCUMENTS_HOME` and `KICAD_CACHE_HOME` to keep it out of the user's home.

The Linux binaries come from the official `kicad/kicad:{version}` Docker image (Debian packages,
sources at https://sources.debian.org), with every library but glibc in `lib/`.

{listed}

Source: `kicad-{version}-source.tar.gz` is attached, and the same tag is upstream at
https://gitlab.com/kicad/code/kicad/-/tags/{version}. KiCad is GPL-3.0-or-later; the bundled
third-party libraries keep their own licenses.

Built by {run_url}
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kicad-version", required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True, help="file with existing release tags, one per line")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--notes", type=Path, default=Path("notes.md"))
    args = parser.parse_args(argv)

    (args.dist / "SHA256SUMS").write_text(sha256sums(args.dist))
    assets = [p.name for p in args.dist.iterdir() if p.is_file()]
    args.notes.write_text(release_notes(args.kicad_version, args.run_url, assets))
    print(next_tag(args.kicad_version, args.tags.read_text().split()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
