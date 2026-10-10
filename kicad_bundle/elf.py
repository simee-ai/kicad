"""ELF (Linux) helpers: DT_NEEDED and resolution inside an extracted image root."""

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from elftools.elf.dynamic import DynamicSection
from elftools.elf.elffile import ELFFile



@dataclass(frozen=True)
class Arch:
    """A Linux architecture the bundle is built for: its name in asset names (linux-<name>), docker's
    --platform, Debian's multiarch triplet (where its libraries live) and glibc's loader."""
    name: str
    docker: str
    multiarch: str
    loader: str

    @property
    def lib_dirs(self) -> tuple[str, ...]:
        # Multiarch first: Debian keeps nearly everything there. /lib is /usr/lib in merged-/usr images.
        return (f"usr/lib/{self.multiarch}", "usr/lib")


ARCHES = {a.name: a for a in (Arch("x86_64", "linux/amd64", "x86_64-linux-gnu", "ld-linux-x86-64.so.2"),
                              Arch("arm64", "linux/arm64", "aarch64-linux-gnu", "ld-linux-aarch64.so.1"))}
# glibc and its loader (Arch.loader) must come from the host (they are tied to each other and the kernel ABI).
GLIBC = {"libc.so.6", "libm.so.6", "libmvec.so.1", "libdl.so.2", "libpthread.so.0",
         "librt.so.1", "libresolv.so.2", "libutil.so.1", "libanl.so.1", "libnsl.so.1", "libBrokenLocale.so.1",
         "libthread_db.so.1", "libc_malloc_debug.so.0"}


def deps(binary: Path) -> list[str]:
    with binary.open("rb") as f:
        dyn = ELFFile(f).get_section_by_name(".dynamic")
        if not isinstance(dyn, DynamicSection):
            return []
        return [t.needed for t in dyn.iter_tags("DT_NEEDED")]


def resolve_in(root: Path, path: str) -> Path:
    """The file under root that path names inside the image, following symlinks (absolute ones
    included) as if root were /. Never leaves root."""
    todo = list(PurePosixPath("/", path).parts[1:])
    resolved = PurePosixPath("/")
    hops = 0
    while todo:
        part = todo.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            resolved = resolved.parent
            continue
        candidate = resolved / part
        host = root / candidate.relative_to("/")
        if host.is_symlink():
            hops += 1
            if hops > 40:
                raise RuntimeError(f"symlink loop resolving {path}")
            target = PurePosixPath(os.readlink(host))
            if target.is_absolute():
                resolved, target = PurePosixPath("/"), target.relative_to("/")
            todo = [*target.parts, *todo]
        else:
            resolved = candidate
    return root / resolved.relative_to("/")


def make_resolver(root: Path, arch: Arch = ARCHES["x86_64"]):
    """Resolve DT_NEEDED names against the image's library dirs for arch; glibc -> None (host-provided).
    An unknown name resolves to a path that doesn't exist, which closure() reports as missing."""
    def resolve(name: str, _binary: Path) -> Path | None:
        if name in GLIBC or name == arch.loader:
            return None
        for d in arch.lib_dirs:
            found = resolve_in(root, f"{d}/{name}")
            if found.is_file():
                return found
        return root / arch.lib_dirs[0] / name

    return resolve
