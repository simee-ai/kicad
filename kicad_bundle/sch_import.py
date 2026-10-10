"""Prove `kicad-cli sch import` (simee/<version> builds) converts real circuits: import each fixture,
export the result's netlist and compare KiCad's components and nets with the source tool's."""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kicad_bundle.smoke import kicad_env, netlist_components, netlist_nets

FIXTURES = Path(__file__).parent / "smoke" / "import"

# Where an import's source and output folders are, relative to one folder: (source, output). The sheet
# files of a multi-sheet design must land next to the output whichever contains the other (#18).
LAYOUTS = {
    "apart": ("src", "out"),
    "output inside source": ("", "kicad/sub"),
    "source inside output": ("src/sub", ""),
}


@dataclass(frozen=True)
class Fixture:
    name: str
    source: Path
    format: str
    components: dict[str, str]
    nets: list[list[str]]
    meta: dict

    @classmethod
    def load(cls, folder: Path) -> "Fixture":
        meta = json.loads((folder / "fixture.json").read_text())
        return cls(folder.name, folder / meta["source"], meta["format"], meta["components"],
                   meta["nets"], meta)


def fixtures() -> list[Fixture]:
    return [Fixture.load(d) for d in sorted(FIXTURES.iterdir()) if (d / "fixture.json").exists()]


def _run(cli: list[str], args: list[str], home: Path) -> None:
    result = subprocess.run([*cli, *args], env=kicad_env(home), capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"kicad-cli {' '.join(args[:2])} failed ({result.returncode}): "
                           f"{(result.stdout + result.stderr).strip()[-2000:]}")


def stray_files(folder: Path) -> list[str]:
    """Hidden files an import left in its output folder, such as a nameless `.kicad_sch` root sheet."""
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("."))


def imported(cli: list[str], fixture: Fixture, layout: str = "apart") -> tuple[dict[str, str], list[list[str]], list[str]]:
    """Components and nets (two or more nodes) of `fixture` as KiCad imports it, its source and output
    folders placed as `layout` says, and the stray files the import wrote next to its output."""
    with tempfile.TemporaryDirectory() as tmp:
        src_dir, out = (Path(tmp) / "job" / d for d in LAYOUTS[layout])
        shutil.copytree(fixture.source.parent, src_dir, dirs_exist_ok=True)
        out.mkdir(parents=True, exist_ok=True)
        sch, net = out / "imported.kicad_sch", Path(tmp) / "imported.net"
        source = src_dir / fixture.source.name
        _run(cli, ["sch", "import", "--format", fixture.format, "-o", str(sch), str(source)], Path(tmp))
        stray = stray_files(out)
        _run(cli, ["sch", "export", "netlist", "-o", str(net), str(sch)], Path(tmp))
        text = net.read_text()
    return netlist_components(text), [n for n in netlist_nets(text) if len(n) > 1], stray


def check(cli: list[str], fixture: Fixture) -> None:
    """Raise unless KiCad's import of `fixture`, in every layout, has exactly the expected components
    and nets, and writes nothing but KiCad files next to its output."""
    problems = []
    for layout in LAYOUTS:
        components, nets, stray = imported(cli, fixture, layout)
        if stray:
            problems.append(f"{layout}: stray files next to the output: {stray}")
        if components != fixture.components:
            problems.append(f"{layout}: components differ: got {components}, wanted {fixture.components}")
        missing = [n for n in fixture.nets if n not in nets]
        extra = [n for n in nets if n not in fixture.nets]
        if missing or extra:
            problems.append(f"{layout}: nets differ: missing {missing}, unexpected {extra}")
    if problems:
        raise RuntimeError(f"{fixture.name}: " + "; ".join(problems))
