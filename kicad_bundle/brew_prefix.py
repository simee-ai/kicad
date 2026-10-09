"""A private Homebrew prefix holding exactly the bottles the official KiCad DMG's libraries came from
(see homebrew.match), poured the way `brew` pours them: each keg in <root>/Cellar/<formula>/<version>,
<root>/opt/<formula> pointing at it, Homebrew's @@HOMEBREW_...@@ placeholders replaced by real paths
in its text files and Mach-O load commands, and (unless keg-only) linked into <root>/include, lib, ...
as `brew link` does: KiCad's build, like any other against Homebrew, relies on that shared include dir. Building against it gives the headers, CMake and pkg-config
files and tools (protoc) of exactly the libraries KiCad's own binaries are linked with."""

import re
import subprocess
import tarfile
from pathlib import Path

from kicad_bundle import macho
from kicad_bundle.bundle import remove
from kicad_bundle.homebrew import Bottle

PREFIX, CELLAR = "@@HOMEBREW_PREFIX@@", "@@HOMEBREW_CELLAR@@"


def relocate_text(data: bytes, root: Path) -> bytes:
    return data.replace(CELLAR.encode(), f"{root}/Cellar".encode()).replace(PREFIX.encode(), str(root).encode())


def placeholder_changes(names: list[str], root: Path) -> list[tuple[str, str]]:
    """(old, new) for each load-command path that names a placeholder."""
    return [(n, relocate_text(n.encode(), root).decode()) for n in names if n.startswith((PREFIX, CELLAR))]


def _relocate_macho(path: Path, root: Path) -> None:
    args = []
    for old, new in placeholder_changes([i for i in [macho.install_id(path)] if i], root):
        args += ["-id", new]
    for old, new in placeholder_changes(macho.deps(path), root):
        args += ["-change", old, new]
    for old, new in placeholder_changes(macho.rpaths(path), root):
        args += ["-rpath", old, new]
    if args:
        path.chmod(path.stat().st_mode | 0o200)
        subprocess.run(["install_name_tool", *args, str(path)], check=True, capture_output=True)
        macho.adhoc_sign(path)


def default_prefix(tag: str) -> str:
    """Where Homebrew built a bottle: binaries keep that path compiled in, which pouring can't change."""
    return "/opt/homebrew" if tag.startswith("arm64_") else "/usr/local"


CONFIG_TOOL = re.compile(r".+[-_]config")


def wrap_config_tool(tool: Path, built_in: str, root: Path) -> None:
    """Replace a compiled *-config tool (odbc_config: KiCad's CMake reads its output) by a script that
    runs it and prints our prefix where it prints the one compiled in."""
    real = tool.with_name(tool.name + ".bottle")
    tool.replace(real)
    tool.write_text(f"""#!/bin/bash
# {tool.name} as poured: it prints {built_in}, where Homebrew built it; this prefix is {root}
set -o pipefail
"{real}" "$@" | sed -e 's#{built_in}/#{root}/#g'
""")
    tool.chmod(0o755)


def pour(bottle: Bottle, archive: Path, root: Path) -> Path:
    """Unpack a bottle into root/Cellar, link root/opt/<formula> to it and relocate it; returns the keg."""
    root = root.resolve()  # its paths go into the keg's files and symlinks
    keg = root / "Cellar" / bottle.formula / bottle.version
    if keg.exists():
        remove(keg)
    with tarfile.open(archive) as tar:
        tar.extractall(root / "Cellar", filter="tar")
    opt = root / "opt" / bottle.formula
    opt.parent.mkdir(parents=True, exist_ok=True)
    opt.unlink(missing_ok=True)
    opt.symlink_to(keg)
    for path in sorted(keg.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if macho.is_macho(path):
            _relocate_macho(path, root)
        elif (PREFIX.encode() in (data := path.read_bytes())) or CELLAR.encode() in data:
            path.chmod(path.stat().st_mode | 0o200)
            path.write_bytes(relocate_text(data, root))
    built_in = default_prefix(bottle.tag)
    for tool in sorted((keg / "bin").glob("*")) if (keg / "bin").is_dir() else []:
        if CONFIG_TOOL.fullmatch(tool.name) and macho.is_macho(tool) and f"{built_in}/".encode() in tool.read_bytes():
            wrap_config_tool(tool, built_in, root)
    if not keg_only((keg / ".brew" / f"{bottle.formula}.rb").read_text()):
        link(keg, root)
    return keg


def use_libraries(keg: Path, libraries: Path, arch: str) -> list[str]:
    """For a keg Homebrew built from source, poured from the bottle standing in for it (the same version
    for another architecture, see homebrew._from_source): replace each library in keg/lib that libraries
    (the official app's Frameworks) has by its arch image there, so the build links what the bundle
    ships. Returns the names replaced."""
    replaced = []
    for lib in sorted((keg / "lib").glob("*.dylib")):
        if lib.is_symlink() or not (official := libraries / lib.name).is_file():
            continue
        lib.chmod(lib.stat().st_mode | 0o200)
        lib.write_bytes(macho.extract(official.read_bytes(), arch))
        replaced.append(lib.name)
    if not replaced:
        raise RuntimeError(f"none of the libraries of {keg} is in {libraries}")
    return replaced


LINKED = ("bin", "include", "lib", "share", "Frameworks")


def keg_only(formula_rb: str) -> bool:
    return re.search(r"^\s*keg_only\b", formula_rb, re.M) is not None


def _link_tree(src: Path, dest: Path) -> None:
    """Symlink src's entries into dest: a whole directory where dest has none, merging where it has one."""
    for entry in sorted(src.iterdir()):
        if entry.name == ".DS_Store":  # Finder's, not the keg's
            continue
        target = dest / entry.name
        if not target.exists() and not target.is_symlink():
            target.symlink_to(entry)
        elif entry.is_dir() and target.is_dir():
            if target.is_symlink():  # another keg's directory: unfold it into a real one first
                other = target.resolve()
                target.unlink()
                target.mkdir()
                _link_tree(other, target)
            _link_tree(entry, target)


def link(keg: Path, root: Path) -> None:
    for top in LINKED:
        if (keg / top).is_dir():
            (root / top).mkdir(parents=True, exist_ok=True)
            _link_tree(keg / top, root / top)
