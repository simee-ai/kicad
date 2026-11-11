"""Prove `kicad-cli pcb tune` (simee/<version> builds, #52) lengthens nets with KiCad's tuner as DRC judges them:
on a small two-layer board (board_text), tune a net to a length and check `pcb drc` against a length rule.
tests/test_pcb_tune.py has the rest of its cases; the smoke test runs check()."""

import json
import subprocess
import tempfile
import uuid
from pathlib import Path

from kicad_bundle.smoke import kicad_env

# The fixture's nets: SIG_A straight (40 mm), SIG_B with a bend (42.07 mm), OBST 1.2 mm beside SIG_A so
# the meanders must keep clear of it, and USB_P/USB_N, a pair of 0.2 mm tracks 0.4 mm apart (USB_N bends
# at its end, 0.41 mm longer).
NETS = ["", "SIG_A", "SIG_B", "OBST", "USB_P", "USB_N"]
PADS = [  # ref, x, y, net
    ("TP1", 5, 10, 1), ("TP2", 45, 10, 1),
    ("TP3", 5, 20, 2), ("TP4", 45, 25, 2),
    ("TP5", 5, 8.8, 3), ("TP6", 45, 8.8, 3),
    ("TP7", 5, 32, 4), ("TP8", 45, 32, 4),
    ("TP9", 5, 32.6, 5), ("TP10", 45, 33.6, 5),
]
TRACKS = [  # x1, y1, x2, y2, net
    (5, 10, 45, 10, 1),
    (5, 20, 25, 20, 2), (25, 20, 30, 25, 2), (30, 25, 45, 25, 2),
    (5, 8.8, 45, 8.8, 3),
    (5, 32, 45, 32, 4),
    (5, 32.6, 43, 32.6, 5), (43, 32.6, 44, 33.6, 5), (44, 33.6, 45, 33.6, 5),
]
# Violations DRC finds on any fixture: its footprints come from no library
IGNORED = {"lib_footprint_issues"}


def rule(name: str, condition: str, constraint: str) -> str:
    return f'(rule "{name}" (condition "{condition}") (constraint {constraint}))'


def length_rule(net: str, low: float, high: float, opt: float | None = None) -> str:
    opt_ = f" (opt {opt}mm)" if opt is not None else ""
    return rule(f"{net} length", f"A.NetName == '{net}'", f"length (min {low}mm){opt_} (max {high}mm)")


def board_text() -> str:
    """The fixture board, the same on every call (its uuids come from its items)."""
    def u(*key) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "simee-kicad pcb tune fixture " + repr(key)))

    out = ['(kicad_pcb (version 20240108) (generator "pcbnew") (generator_version "8.0")',
           ' (general (thickness 1.6) (legacy_teardrops no)) (paper "A4")',
           ' (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (36 "B.SilkS" user "B.Silkscreen")'
           ' (37 "F.SilkS" user "F.Silkscreen") (38 "B.Mask" user) (39 "F.Mask" user)'
           ' (35 "F.Paste" user) (44 "Edge.Cuts" user) (47 "F.CrtYd" user "F.Courtyard") (49 "F.Fab" user))',
           ' (setup (pad_to_mask_clearance 0))']
    out += [f' (net {i} "{n}")' for i, n in enumerate(NETS)]
    for ref, x, y, net in PADS:
        size = 0.4 if net >= 4 else 0.8
        out.append(f' (footprint "simee:TP" (layer "F.Cu") (uuid "{u(ref)}") (at {x} {y})'
                   f' (property "Reference" "{ref}" (at 0 0 0) (layer "F.Fab") (uuid "{u(ref, "ref")}")'
                   f' (effects (font (size 0.5 0.5) (thickness 0.1))))'
                   f' (property "Value" "TP" (at 0 0 0) (layer "F.Fab") (hide yes) (uuid "{u(ref, "value")}")'
                   f' (effects (font (size 0.5 0.5) (thickness 0.1))))'
                   f' (attr smd)'
                   f' (pad "1" smd rect (at 0 0) (size {size} {size}) (layers "F.Cu" "F.Mask")'
                   f' (net {net} "{NETS[net]}") (uuid "{u(ref, "pad")}")))')
    for i, (x1, y1, x2, y2, net) in enumerate(TRACKS):
        out.append(f' (segment (start {x1} {y1}) (end {x2} {y2}) (width {0.2 if net >= 4 else 0.25})'
                   f' (layer "F.Cu") (net {net}) (uuid "{u("track", i)}"))')
    out.append(f' (gr_rect (start 0 0) (end 60 40) (stroke (width 0.1) (type default)) (fill none)'
               f' (layer "Edge.Cuts") (uuid "{u("outline")}"))')
    return "\n".join(out) + "\n)\n"


def project(folder: Path, name: str, rules: list[str], board: str | None = None) -> Path:
    """folder/name.kicad_pcb (board, else the fixture) with a project and the DRC rules given; returns it."""
    pcb = folder / f"{name}.kicad_pcb"
    pcb.write_text(board if board is not None else board_text(), encoding="utf-8")
    pcb.with_suffix(".kicad_pro").write_text("{}\n", encoding="utf-8")
    set_rules(pcb, rules)
    return pcb


def set_rules(pcb: Path, rules: list[str]) -> None:
    pcb.with_suffix(".kicad_dru").write_text("(version 1)\n" + "".join(r + "\n" for r in rules), encoding="utf-8")


def tune(cli: list[str], pcb: Path, *args: str, output: Path | None = None) -> tuple[int, dict]:
    """`pcb tune` of pcb (in place, or to output) with args: its exit code and JSON report, by net."""
    report = pcb.with_name(pcb.stem + "-tune.json")
    report.unlink(missing_ok=True)
    result = subprocess.run([*cli, "pcb", "tune", *args, "--format", "json", "--report-file", str(report),
                             *(["-o", str(output)] if output else []), str(pcb)],
                            env=kicad_env(pcb.parent), capture_output=True, encoding="utf-8", errors="replace")
    if not report.exists():
        raise RuntimeError(f"pcb tune wrote no report ({result.returncode}): {(result.stdout + result.stderr)[-2000:]}")
    return result.returncode, {n["net"]: n for n in json.loads(report.read_text(encoding="utf-8"))["nets"]}


def drc(cli: list[str], pcb: Path, refill: bool = False) -> list[dict]:
    """`pcb drc` of pcb: its violations (unconnected items among them), less IGNORED's."""
    out = pcb.with_name(pcb.stem + "-drc.json")
    subprocess.run([*cli, "pcb", "drc", "--format", "json", "--severity-all", *(["--refill-zones"] if refill else []),
                    "-o", str(out), str(pcb)], env=kicad_env(pcb.parent), capture_output=True)
    report = json.loads(out.read_text(encoding="utf-8"))
    return [v for v in report["violations"] + report["unconnected_items"] if v["type"] not in IGNORED]


def violations(cli: list[str], pcb: Path, refill: bool = False) -> list[str]:
    """The types of pcb's DRC violations, sorted."""
    return sorted(v["type"] for v in drc(cli, pcb, refill))


def patterns(pcb: Path) -> int:
    """How many tuning patterns pcb holds."""
    return pcb.read_text(encoding="utf-8").count("(type tuning_pattern)")


def check(cli: list[str]) -> None:
    """Raise unless `pcb tune` lengthens SIG_A from 40 to 45 mm with a tuning pattern, DRC's length rule
    failing before and passing after."""
    with tempfile.TemporaryDirectory() as tmp:
        pcb = project(Path(tmp), "tune", [length_rule("SIG_A", 44.9, 45.1)])
        before = violations(cli, pcb)
        code, nets = tune(cli, pcb, "--net", "SIG_A", "--length", "45")
        after = violations(cli, pcb)
        if before != ["length_out_of_range"] or code != 0 or after or patterns(pcb) != 1:
            raise RuntimeError(f"pcb tune --net SIG_A --length 45: exit {code}, report {nets.get('SIG_A')}, "
                               f"DRC before {before} and after {after}, {patterns(pcb)} tuning patterns")
