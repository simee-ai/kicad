"""What the Arm and wasm toolchain packagers share: choosing the files of an upstream archive by path patterns,
and taking those members out of it with their links intact."""

import tarfile
from fnmatch import fnmatchcase
from typing import Callable, Iterable


def matches(rel: str, patterns: Iterable[str]) -> bool:
    """Whether rel (a path below the archive's top folder), or a folder above it, matches one of patterns, one path
    component at a time: a pattern naming a folder matches everything in it."""
    parts = rel.split("/")
    for pattern in patterns:
        wanted = pattern.split("/")
        if len(parts) >= len(wanted) and all(map(fnmatchcase, parts, wanted)):
            return True
    return False


def top(names: list[str]) -> str:
    """The archive's one top folder, with a trailing /, or "" (Arm's Windows zip has none)."""
    tops = {n.partition("/")[0] for n in names}
    return f"{tops.pop()}/" if len(tops) == 1 else ""


def kept_members(members: list[tarfile.TarInfo], top: str, kept: Callable[[str], bool]) -> list[tarfile.TarInfo]:
    """The members kept() keeps (by their path below top), renamed below top. A hard link names its target by its
    path in the archive (a symbolic one relative to itself); one whose target isn't kept (Arm's Linux
    arm-none-eabi-g++ links to arm-none-eabi-c++ from 15.3) takes the target's data, and further links to that
    target link to it."""
    by_name = {m.name: m for m in members}
    holder: dict[str, str] = {}  # a target that isn't kept -> the kept link holding its data
    out = []
    for m in members:
        rel = m.name.removeprefix(top)
        if m.isdir() or not kept(rel):
            continue
        if m.islnk() and not kept(target := m.linkname.removeprefix(top)):
            if target in holder:
                out.append(m.replace(name=rel, linkname=holder[target], deep=False))
            else:
                holder[target] = rel
                out.append(by_name[m.linkname].replace(name=rel, deep=False))
            continue
        out.append(m.replace(name=rel, deep=False, linkname=m.linkname.removeprefix(top) if m.islnk() else m.linkname))
    return out
