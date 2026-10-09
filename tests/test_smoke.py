import json
import sys
from pathlib import Path

import pytest

from kicad_bundle import linux, macos, smoke, windows_build
from kicad_bundle.smoke import EXPECTED, EXPECTED_ERC, erc_errors, netlist_nets

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


FAKE_CLI = """import sys
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("-o") + 1])
if args[:2] == ["sch", "erc"]:
    {erc}
else:
    out.write_text({netlist!r})
"""


def _fake_cli(tmp_path: Path, erc: str) -> list[str]:
    script = tmp_path / "kicad_cli.py"
    script.write_text(FAKE_CLI.format(erc=erc, netlist=NETLIST))
    return [sys.executable, str(script)]


def test_check_runs_erc_too(tmp_path):
    smoke.check(_fake_cli(tmp_path, f"out.write_text({ERC_REPORT!r})"))


def test_check_fails_when_erc_cant_load_a_kiface(tmp_path):
    # The trimmed bundle's failure before cvpcb was bundled: ERC exits 1 and writes no report.
    erc = ("print(\"Error: Failed to load kiface library '/x/PlugIns/_cvpcb.kiface'.\", file=sys.stderr); "
           "sys.exit(1)")
    with pytest.raises(RuntimeError, match="_cvpcb.kiface"):
        smoke.check(_fake_cli(tmp_path, erc))


def test_check_fails_when_erc_finds_other_errors(tmp_path):
    report = json.dumps({"sheets": [{"violations": [_violation("pin_not_connected", "error", "Symbol R1 Pin 2")]}]})
    with pytest.raises(RuntimeError, match="pin_not_connected"):
        smoke.check(_fake_cli(tmp_path, f"out.write_text({report!r})"))


def test_every_platform_bundles_and_builds_the_kiface_erc_needs():
    # `sch erc` loads cvpcb's kiface for its footprint checks; without it ERC fails (simee-kicad#7).
    assert "PlugIns/_cvpcb.kiface" in macos.ROOTS
    assert "usr/bin/_cvpcb.kiface" in linux.ROOTS
    assert "cvpcb_kiface" in windows_build.TARGETS
