import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

from kicad_bundle import linux, macos, smoke, windows_build
from kicad_bundle.smoke import (EXPECTED, EXPECTED_ERC, EXPECTED_FOOTPRINTS, EXPECTED_HOLES, drill_holes, erc_errors,
                                gerber_nets, netlist_nets)

NETLIST = """(export (version "E")
  (nets
    (net (code "1") (name "/IN") (class "Default") (node (ref "R1") (pin "1") (pintype "passive")))
    (net (code "2") (name "/OUT") (class "Default")
      (node (ref "C1") (pin "2") (pinfunction "~_2") (pintype "passive"))
      (node (ref "R1") (pin "2") (pintype "passive")))
    (net (code "3") (name "GND") (class "Default") (node (ref "C1") (pin "1") (pintype "passive")))))
"""


def _violation(type_: str, severity: str, item: str) -> dict:
    return {"type": type_, "severity": severity, "description": "", "items": [{"description": item}]}


# What kicad-cli 10.0.6 reports for the smoke schematic (`sch erc --format json --severity-all`).
ERC_REPORT = json.dumps({"$schema": "https://schemas.kicad.org/erc.v1.json", "sheets": [{"path": "/", "violations": [
    _violation("power_pin_not_driven", "error", "Symbol #PWR01 Pin 1 [Power input, Line]"),
    _violation("endpoint_off_grid", "warning", "Symbol R1 Pin 1 [Passive, Line]"),
    _violation("lib_symbol_issues", "warning", "Symbol C1 [C]"),
]}]})


def test_netlist_nets_reads_kicadsexpr():
    assert netlist_nets(NETLIST) == EXPECTED


def test_erc_errors_reads_the_error_violations_of_a_json_report():
    assert erc_errors(ERC_REPORT) == EXPECTED_ERC


# Trimmed from kicad-cli 10.0.6's `pcb export gerbers` of the smoke board (F_Cu; B_Cu has the same pads).
GERBER = """%TF.FileFunction,Copper,L1,Top*%
%FSLAX46Y46*%
D10*
%TO.P,R1,1*%
%TO.N,/IN*%
X105000000Y-100000000D03*
D11*
%TO.P,R1,2*%
%TO.N,/OUT*%
X110080000Y-100000000D03*
%TD*%
D10*
%TO.P,C1,1*%
%TO.N,GND*%
X115160000Y-100000000D03*
%TO.P,C1,2,~_2*%
%TO.N,/OUT*%
X112620000Y-100000000D03*
%TD*%
%TO.N,/OUT*%
D12*
X110080000Y-100000000D02*
X112620000Y-100000000D01*
%TD*%
M02*
"""
EDGE_CUTS = "%TF.FileFunction,Profile,NP*%\nM02*\n"
DRILL = """M48
METRIC
T1C0.800
%
G90
G05
T1
X105.0Y-100.0
X110.08Y-100.0
X112.62Y-100.0
X115.16Y-100.0
M30
"""
FOOTPRINT = '(footprint "R_Axial_P5.08mm"\n\t(version 20260206)\n)\n'
STEP = "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"

# What each command writes: (file under its -o folder, or "" when -o is the file; text).
OUTPUTS = {
    "sch export netlist": [("", NETLIST)],
    "sch erc": [("", ERC_REPORT)],
    "fp upgrade": [("R_Axial_P5.08mm.kicad_mod", FOOTPRINT)],
    "pcb export gerbers": [("rc_filter-F_Cu.gtl", GERBER), ("rc_filter-B_Cu.gbl", GERBER.replace("L1,Top", "L2,Bot")),
                           ("rc_filter-Edge_Cuts.gm1", EDGE_CUTS)],
    "pcb export drill": [("rc_filter.drl", DRILL)],
    "pcb export step": [("", STEP)],
}

FAKE_CLI = """import sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("-o") + 1])
outputs = {outputs!r}
cmd = next(c for c in outputs if args[:len(c.split())] == c.split())
if cmd == {fail!r}:
    {code}
for name, text in outputs[cmd]:
    if name:
        out.mkdir(parents=True, exist_ok=True)
    (out / name if name else out).write_text(text)
"""


def _fake_cli(tmp_path: Path, fail: str = "", code: str = "pass", **outputs: list) -> list[str]:
    """A kicad-cli that writes OUTPUTS (or outputs, keyed by command with _ for spaces), except that
    the command fail runs code first."""
    script = tmp_path / "kicad_cli.py"
    script.write_text(FAKE_CLI.format(outputs={**OUTPUTS, **{k.replace("_", " "): v for k, v in outputs.items()}},
                                      fail=fail, code=code))
    return [sys.executable, str(script)]


def test_gerber_nets_reads_the_pads_of_each_net():
    assert gerber_nets([GERBER, GERBER]) == EXPECTED


def test_drill_holes_counts_the_holes_of_an_excellon_file():
    assert drill_holes(DRILL) == EXPECTED_HOLES


def test_check_runs_erc_and_the_pcb_commands(tmp_path):
    smoke.check(_fake_cli(tmp_path))


def test_check_fails_when_erc_cant_load_a_kiface(tmp_path):
    # The trimmed bundle's failure before cvpcb was bundled: ERC exits 1 and writes no report.
    erc = ("print(\"Error: Failed to load kiface library '/x/PlugIns/_cvpcb.kiface'.\", file=sys.stderr); "
           "sys.exit(1)")
    with pytest.raises(RuntimeError, match="_cvpcb.kiface"):
        smoke.check(_fake_cli(tmp_path, "sch erc", erc))


@pytest.mark.parametrize("command", ["fp upgrade", "pcb export gerbers", "pcb export drill", "pcb export step"])
def test_check_fails_when_a_pcb_command_cant_load_pcbnew(tmp_path, command):
    # The trimmed bundle's failure before pcbnew was bundled (simee-kicad#8): exit 255, nothing written.
    fail = ("print(\"Error: Failed to load kiface library '/x/PlugIns/_pcbnew.kiface'.\", file=sys.stderr); "
            "sys.exit(255)")
    with pytest.raises(RuntimeError, match="_pcbnew.kiface"):
        smoke.check(_fake_cli(tmp_path, command, fail))


def test_check_fails_when_the_gerbers_lose_a_pad(tmp_path):
    gerber = GERBER.replace("%TO.N,GND*%", "%TO.N,/OUT*%")
    with pytest.raises(RuntimeError, match="gerber"):
        smoke.check(_fake_cli(tmp_path, pcb_export_gerbers=[("rc_filter-F_Cu.gtl", gerber),
                                                            ("rc_filter-Edge_Cuts.gm1", EDGE_CUTS)]))


def test_check_fails_without_a_board_outline(tmp_path):
    with pytest.raises(RuntimeError, match="Edge_Cuts"):
        smoke.check(_fake_cli(tmp_path, pcb_export_gerbers=[("rc_filter-F_Cu.gtl", GERBER)]))


def test_check_fails_when_fp_upgrade_leaves_the_old_format(tmp_path):
    old = "(module R_Axial_P5.08mm (layer F.Cu))\n"
    with pytest.raises(RuntimeError, match="fp upgrade"):
        smoke.check(_fake_cli(tmp_path, fp_upgrade=[("R_Axial_P5.08mm.kicad_mod", old)]))


def test_check_fails_on_a_step_file_that_isnt_one(tmp_path):
    with pytest.raises(RuntimeError, match="STEP"):
        smoke.check(_fake_cli(tmp_path, pcb_export_step=[("", "")]))


def test_the_smoke_board_is_the_rc_filters_layout():
    # Its pads carry the schematic's nets, so gerber_nets of its copper layers is EXPECTED.
    board = smoke.parse_sexpr(smoke.BOARD.read_text())
    pads = defaultdict(list)
    for fp in (c for c in board if isinstance(c, list) and c[0] == "footprint"):
        ref = next(c[2] for c in fp if isinstance(c, list) and c[:2] == ["property", "Reference"])
        for pad in (c for c in fp if isinstance(c, list) and c[0] == "pad"):
            pads[next(c[1] for c in pad if isinstance(c, list) and c[0] == "net")].append(f"{ref}.{pad[1]}")
    assert sorted(sorted(p) for p in pads.values()) == EXPECTED
    assert len([p for pins in pads.values() for p in pins]) == EXPECTED_HOLES  # every pad is through-hole
    assert sorted(p.stem for p in smoke.FOOTPRINTS.glob("*.kicad_mod")) == EXPECTED_FOOTPRINTS


@pytest.mark.parametrize("kiface", ["cvpcb", "pcbnew"])
def test_every_platform_bundles_and_builds_the_kifaces_simee_runs(kiface):
    # `sch erc` loads cvpcb's kiface for its footprint checks (simee-kicad#7); every `fp` and `pcb`
    # command loads pcbnew's (simee-kicad#8). Without them those commands fail.
    assert f"PlugIns/_{kiface}.kiface" in macos.ROOTS
    assert f"usr/bin/_{kiface}.kiface" in linux.ROOTS
    assert f"{kiface}_kiface" in windows_build.TARGETS
