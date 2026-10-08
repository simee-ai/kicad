"""The simee-kicad commit a bundle's KiCad files are built from (a simee/<version> branch, see README),
and its source tarball: the release's GPL source of those files."""

import subprocess
from pathlib import Path

from kicad_bundle.fetch import fetch_url, write_atomically

REPO = "https://github.com/simee-ai/simee-kicad"


def resolve_ref(ref: str) -> str:
    """The commit a simee-kicad branch, tag or commit names."""
    out = subprocess.run(["git", "ls-remote", REPO, ref], capture_output=True, text=True, check=True).stdout.split()
    if out:
        return out[0]
    if len(ref) == 40 and all(c in "0123456789abcdef" for c in ref):
        return ref
    raise RuntimeError(f"{REPO} has no ref {ref}")


def source_archive(sha: str, dest: Path) -> Path:
    """GitHub's tarball of simee-kicad at sha: what the binaries are built from, and the release's
    GPL source."""
    if not dest.exists():
        data = fetch_url(f"{REPO}/archive/{sha}.tar.gz")
        write_atomically(dest, lambda f: f.write(data))
    return dest
