"""Trimming a copied KiCad tree down to a closure, and archiving the result."""

import shutil
import tarfile
import time
from pathlib import Path


def prune(tops: list[Path], keep: set[Path]) -> None:
    """Under each top dir delete every file not in keep, every symlink that doesn't lead to a kept
    file (or a dir holding one), then every directory left empty."""
    keep = {k.resolve() for k in keep}
    for top in tops:
        for path in sorted(top.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_symlink():
                target = path.resolve()
                if not any(k == target or k.is_relative_to(target) for k in keep):
                    path.unlink()
            elif path.is_file() and path.resolve() not in keep:
                path.unlink()
        for path in sorted(top.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
                path.rmdir()


def archive(root: Path, out_dir: Path, fmt: str) -> Path:
    """root/ as out_dir/<root name>.tar.gz or .zip, with root's name as the top-level folder."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if fmt == "tar.gz":
        dest = out_dir / f"{root.name}.tar.gz"
        with tarfile.open(dest, "w:gz") as tar:
            tar.add(root, arcname=root.name)  # keeps symlinks as symlinks
        return dest
    dest = Path(shutil.make_archive(str(out_dir / root.name), "zip", root.parent, root.name))
    return dest


def copy_tree(src: Path, dest: Path, exclude: tuple[str, ...] = ()) -> None:
    """Copy src to dest preserving symlinks, skipping paths (relative to src) in exclude."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        rel = Path(directory).relative_to(src)
        return {n for n in names if str(rel / n) in exclude}

    shutil.copytree(src, dest, symlinks=True, ignore=ignore)


def remove(path: Path, tries: int = 5) -> None:
    """rmtree that also removes read-only files and folders (Homebrew bottles ship some), retried
    because Finder may drop a .DS_Store into a folder while it's being deleted."""
    def writable(func, name, _):
        Path(name).parent.chmod(0o755)
        Path(name).chmod(0o755)
        func(name)

    for attempt in range(tries):
        try:
            shutil.rmtree(path, onerror=writable)
            return
        except OSError:
            if attempt == tries - 1:
                raise
            time.sleep(1)
