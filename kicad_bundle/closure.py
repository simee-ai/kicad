"""Transitive shared-library closure, independent of the binary format."""

from pathlib import Path
from typing import Callable, Iterable


def closure(roots: Iterable[Path], deps: Callable[[Path], list[str]],
            resolve: Callable[[str, Path], Path | None],
            exists: Callable[[Path], bool] = Path.exists,
            missing: list[tuple[str, str]] | None = None) -> set[Path]:
    """Every file reachable from roots. resolve() maps a dependency name to a file inside the
    bundle, or None for a system library that isn't shipped. Unresolvable names go to missing."""
    seen: set[Path] = set()
    todo = list(roots)
    while todo:
        binary = todo.pop()
        if binary in seen:
            continue
        seen.add(binary)
        for name in deps(binary):
            target = resolve(name, binary)
            if target is None:
                continue
            if exists(target):
                todo.append(target)
            elif missing is not None:
                missing.append((binary.name, name))
    return seen
