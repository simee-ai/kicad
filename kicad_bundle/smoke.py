"""Prove a bundle works: export the netlist of a known RC filter and compare KiCad's nets, run KiCad's
electrical rules check on it and compare the errors, then run what pcbnew does (upgrade a footprint
library; export the filter's board as gerbers, drill and STEP) and check each output."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

SCHEMATIC = Path(__file__).parent / "smoke" / "rc_filter.kicad_sch"
EXPECTED = [["C1.1"], ["C1.2", "R1.2"], ["R1.1"]]
# ERC's errors (type, first item) on it: nothing drives its GND symbol's power input. Its warnings depend
# on the symbol libraries installed, which the bundle has none of.
EXPECTED_ERC = [("power_pin_not_driven", "Symbol #PWR01 Pin 1 [Power input, Line]")]
# The filter's board: its copper pads carry the schematic's nets (EXPECTED), each through a plated hole.
BOARD = SCHEMATIC.with_suffix(".kicad_pcb")
EXPECTED_HOLES = 4
# A footprint library in KiCad 5's format, which `fp upgrade` rewrites in the current one.
FOOTPRINTS = SCHEMATIC.parent / "footprints.pretty"
EXPECTED_FOOTPRINTS = ["R_Axial_P5.08mm"]
# Where KiCad writes config, documents and caches; point them at a private dir.
KICAD_HOMES = ("KICAD_CONFIG_HOME", "KICAD_DOCUMENTS_HOME", "KICAD_CACHE_HOME")
UNESCAPE = re.compile(r"\\(.)")
TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+')
# A gerber X2 pad: its pad attribute (ref, pin[, function]) then its net attribute.
GERBER_PAD = re.compile(r"%TO\.P,([^,*]+),([^,*]+)[^*]*\*%\s*%TO\.N,([^*]*)\*%")
DRILL_HOLE = re.compile(r"^X-?[\d.]+Y-?[\d.]+$", re.M)


def parse_sexpr(text: str) -> list:
    stack: list[list] = [[]]
    for tok in TOKEN.findall(text):
        if tok == "(":
            stack.append([])
        elif tok == ")":
            done = stack.pop()
            stack[-1].append(done)
        else:
            stack[-1].append(UNESCAPE.sub(r"\1", tok[1:-1]) if tok.startswith('"') else tok)
    return stack[0][0]


def _children(node: list, head: str) -> list[list]:
    return [c for c in node if isinstance(c, list) and c and c[0] == head]


def netlist_nets(text: str) -> list[list[str]]:
    """Nets from a kicadsexpr netlist as sorted "REF.PIN" lists."""
    nets = []
    for net in _children(_children(parse_sexpr(text), "nets")[0], "net"):
        pins = sorted(f"{_children(n, 'ref')[0][1]}.{_children(n, 'pin')[0][1]}" for n in _children(net, "node"))
        if pins:
            nets.append(pins)
    return sorted(nets)


def netlist_components(text: str) -> dict[str, str]:
    """Components (ref -> value) of a kicadsexpr netlist; KiCad leaves power symbols out."""
    comps = _children(parse_sexpr(text), "components")
    return {_children(c, "ref")[0][1]: _children(c, "value")[0][1]
            for c in (_children(comps[0], "comp") if comps else [])}


def kicad_env(home: Path) -> dict[str, str]:
    """Keep KiCad's config, document and cache dirs out of the user's home."""
    return {**os.environ, **{k: str(home / k) for k in KICAD_HOMES}}


def erc_errors(report: str) -> list[tuple[str, str]]:
    """(type, first item's description) of each error in a kicad-cli JSON ERC report, sorted."""
    return sorted((v["type"], v["items"][0]["description"] if v.get("items") else "")
                  for sheet in json.loads(report)["sheets"] for v in sheet["violations"] if v["severity"] == "error")


def gerber_nets(gerbers: list[str]) -> list[list[str]]:
    """Nets as sorted "REF.PIN" lists, from the pad attributes of gerber X2 copper layers."""
    nets = defaultdict(set)
    for text in gerbers:
        for ref, pin, net in GERBER_PAD.findall(text):
            nets[net].add(f"{ref}.{pin}")
    return sorted(sorted(pins) for pins in nets.values())


def drill_holes(excellon: str) -> int:
    return len(DRILL_HOLE.findall(excellon))


def _run(cli: list[str], args: list[str], out: Path) -> Path:
    """Run `cli args`, which must write out (a file or folder) without logging errors; returns out."""
    result = subprocess.run([*cli, *args], env=kicad_env(out.parent), capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"kicad-cli {' '.join(args[:2])} failed ({result.returncode}): "
                           f"{(result.stdout + result.stderr).strip()[-2000:]}")
    # KiCad logs some problems (missing data files, libraries) as errors yet still exports.
    errors = [line for line in result.stderr.splitlines() if "Error:" in line]
    if errors:
        raise RuntimeError(f"kicad-cli {' '.join(args[:2])} reported errors:\n" + "\n".join(errors))
    return out


def _check_pcbnew(cli: list[str], tmp: Path) -> None:
    """Raise unless pcbnew's commands work: `fp upgrade` rewrites FOOTPRINTS in the current format, and
    BOARD's gerbers have its nets' pads and its outline, its drill file its holes and its STEP file a header."""
    up = _run(cli, ["fp", "upgrade", "-o", str(tmp / "upgraded.pretty"), str(FOOTPRINTS)], tmp / "upgraded.pretty")
    upgraded = sorted(p.stem for p in up.glob("*.kicad_mod") if p.read_text().startswith("(footprint "))
    if upgraded != EXPECTED_FOOTPRINTS:
        raise RuntimeError(f"fp upgrade wrote footprints {upgraded} in the current format, wanted {EXPECTED_FOOTPRINTS}")

    # A copy: with a fresh config, pcbnew writes the board's project settings (.kicad_prl) next to it.
    board = Path(shutil.copy2(BOARD, tmp))
    gerbers = _run(cli, ["pcb", "export", "gerbers", "-o", f"{tmp / 'gerbers'}/", str(board)], tmp / "gerbers")
    texts = {p.name: p.read_text() for p in gerbers.iterdir()}
    nets = gerber_nets([t for t in texts.values() if "%TF.FileFunction,Copper," in t])
    if nets != EXPECTED:
        raise RuntimeError(f"unexpected gerber pads {nets}, wanted {EXPECTED}")
    if not any("%TF.FileFunction,Profile," in t for t in texts.values()):
        raise RuntimeError(f"no board outline (Edge_Cuts) among the gerbers {sorted(texts)}")

    drill = _run(cli, ["pcb", "export", "drill", "-o", f"{tmp / 'drill'}/", str(board)], tmp / "drill")
    holes = sum(drill_holes(p.read_text()) for p in drill.glob("*.drl"))
    if holes != EXPECTED_HOLES:
        raise RuntimeError(f"the drill files have {holes} holes, wanted {EXPECTED_HOLES}")

    # STEP goes through opencascade, the bulk of pcbnew's libraries.
    step = _run(cli, ["pcb", "export", "step", "-o", str(tmp / "board.step"), str(board)], tmp / "board.step")
    if not step.read_text().startswith("ISO-10303-21;"):
        raise RuntimeError("pcb export step wrote no STEP file")


def check(cli: list[str]) -> None:
    """Raise unless `cli sch export netlist` reproduces the expected nets, `cli sch erc` the expected
    errors (ERC also needs the cvpcb kiface) and pcbnew's commands their outputs (_check_pcbnew)."""
    with tempfile.TemporaryDirectory() as tmp:
        net, erc = Path(tmp) / "smoke.net", Path(tmp) / "smoke-erc.json"
        nets = netlist_nets(_run(cli, ["sch", "export", "netlist", "-o", str(net), str(SCHEMATIC)], net).read_text())
        errors = erc_errors(_run(cli, ["sch", "erc", "--format", "json", "--severity-all", "-o", str(erc),
                                       str(SCHEMATIC)], erc).read_text())
        if nets != EXPECTED:
            raise RuntimeError(f"unexpected nets {nets}, wanted {EXPECTED}")
        if errors != EXPECTED_ERC:
            raise RuntimeError(f"unexpected ERC errors {errors}, wanted {EXPECTED_ERC}")
        _check_pcbnew(cli, Path(tmp))
