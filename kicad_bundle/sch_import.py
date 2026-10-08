"""Prove `kicad-cli sch import` (simee/<version> builds) converts real circuits: import each fixture,
export the result's netlist and compare KiCad's components and nets with the source tool's."""

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kicad_bundle.smoke import kicad_env, netlist_components, netlist_nets

FIXTURES = Path(__file__).parent / "smoke" / "import"


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


def imported(cli: list[str], fixture: Fixture) -> tuple[dict[str, str], list[list[str]]]:
    """Components and nets (two or more nodes) of `fixture` as KiCad imports it."""
    with tempfile.TemporaryDirectory() as tmp:
        sch, net = Path(tmp) / "imported.kicad_sch", Path(tmp) / "imported.net"
        _run(cli, ["sch", "import", "--format", fixture.format, "-o", str(sch), str(fixture.source)], Path(tmp))
        _run(cli, ["sch", "export", "netlist", "-o", str(net), str(sch)], Path(tmp))
        text = net.read_text()
    return netlist_components(text), [n for n in netlist_nets(text) if len(n) > 1]


def check(cli: list[str], fixture: Fixture) -> None:
    """Raise unless KiCad's import of `fixture` has exactly the expected components and nets."""
    components, nets = imported(cli, fixture)
    problems = []
    if components != fixture.components:
        problems.append(f"components differ: got {components}, wanted {fixture.components}")
    missing = [n for n in fixture.nets if n not in nets]
    extra = [n for n in nets if n not in fixture.nets]
    if missing or extra:
        problems.append(f"nets differ: missing {missing}, unexpected {extra}")
    if problems:
        raise RuntimeError(f"{fixture.name}: " + "; ".join(problems))
