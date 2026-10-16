"""Prove `kicad-cli sch import` (simee/<version> builds) converts real circuits: import each fixture,
export the result's netlist and compare KiCad's components and nets with the source tool's. And that
`--format auto` (what simee-core runs) picks each file's own importer, or none for a file that is no
schematic."""

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kicad_bundle.smoke import kicad_env, netlist_components, netlist_nets

FIXTURES = Path(__file__).parent / "smoke" / "import"
# Files only `--format auto`'s choice of importer is checked on (detect.json lists them).
DETECT = Path(__file__).parent / "smoke" / "detect"

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


# The formats `sch import --format` takes a hint for. Any other (gEDA) is found by the file's content.
FORMAT_HINTS = {"altium", "eagle", "cadstar", "easyeda", "easyedapro", "ltspice", "pads"}


def format_hint(format: str) -> str:
    return format if format in FORMAT_HINTS else "auto"


# What `sch import` calls each format's importer (SCH_IO_MGR::ShowType).
FORMAT_NAMES = {
    "altium": "Altium",
    "cadstar": "CADSTAR Schematic Archive",
    "eagle": "EAGLE",
    "easyeda": "EasyEDA (JLCEDA) Std",
    "easyedapro": "EasyEDA (JLCEDA) Pro",
    "geda": "gEDA / Lepton EDA",
    "ltspice": "LTspice",
    "pads": "PADS Logic",
}


@dataclass(frozen=True)
class Detection:
    """`source` imported with `--format auto` as a file called `name` (re-encoded as UTF-16LE, as LTspice
    XVII saves, when `utf16`) must go to `format`'s importer, or to none when `format` is None."""
    source: Path
    name: str
    format: str | None
    utf16: bool = False

    @property
    def label(self) -> str:
        return self.name + (" (UTF-16LE)" if self.utf16 else "")


def fixtures() -> list[Fixture]:
    return [Fixture.load(d) for d in sorted(FIXTURES.iterdir()) if (d / "fixture.json").exists()]


def detections() -> list[Detection]:
    cases = []
    for f in fixtures():
        cases.append(Detection(f.source, f.source.name, f.format))
        if f.format == "ltspice":
            cases.append(Detection(f.source, f.source.name, f.format, utf16=True))
    for case in json.loads((DETECT / "detect.json").read_text())["cases"]:
        cases.append(Detection(DETECT / case["source"], case.get("name", case["source"]), case["format"]))
    return cases


def detected(cli: list[str], case: Detection) -> str | None:
    """The importer `sch import --format auto` picks for `case` (KiCad's name for it), or None when none
    recognizes the file. The import itself may fail: only the choice counts."""
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "src", Path(tmp) / "out"
        shutil.copytree(case.source.parent, src)
        out.mkdir()
        data = case.source.read_bytes()
        (src / case.name).write_bytes(data.decode().encode("utf-16-le") if case.utf16 else data)
        result = subprocess.run([*cli, "sch", "import", "--format", "auto", "-o", str(out / "imported.kicad_sch"),
                                 str(src / case.name)], env=kicad_env(Path(tmp)), capture_output=True, text=True)
    text = result.stdout + result.stderr
    if "No schematic importer recognizes" in text:
        return None
    if m := re.search(r"using (.+?) format\.\.\.", text):
        return m.group(1)
    raise RuntimeError(f"kicad-cli sch import of {case.label} names no importer ({result.returncode}): "
                       f"{text.strip()[-2000:]}")


def check_detection(cli: list[str]) -> None:
    """Raise unless `--format auto` picks every detection case's own importer."""
    problems = []
    for case in detections():
        got, wanted = detected(cli, case), FORMAT_NAMES.get(case.format)
        if got != wanted:
            problems.append(f"{case.label}: imported as {got or 'no importer'}, wanted {wanted or 'no importer'}")
    if problems:
        raise RuntimeError("sch import --format auto: " + "; ".join(problems))


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
        _run(cli, ["sch", "import", "--format", format_hint(fixture.format), "-o", str(sch), str(source)],
             Path(tmp))
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
