"""Prove a bundle works: export the netlist of a known RC filter and compare KiCad's nets, then run
KiCad's electrical rules check on it and compare the errors."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

SCHEMATIC = Path(__file__).parent / "smoke" / "rc_filter.kicad_sch"
EXPECTED = [["C1.1"], ["C1.2", "R1.2"], ["R1.1"]]
# ERC's errors (type, first item) on it: nothing drives its GND symbol's power input. Its warnings depend
# on the symbol libraries installed, which the bundle has none of.
EXPECTED_ERC = [("power_pin_not_driven", "Symbol #PWR01 Pin 1 [Power input, Line]")]
# Where KiCad writes config, documents and caches; point them at a private dir.
KICAD_HOMES = ("KICAD_CONFIG_HOME", "KICAD_DOCUMENTS_HOME", "KICAD_CACHE_HOME")
UNESCAPE = re.compile(r"\\(.)")
TOKEN = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+')


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


def _run(cli: list[str], args: list[str], out: Path) -> str:
    """Run `cli args`, which must write out without logging errors; returns out's text."""
    result = subprocess.run([*cli, *args], env=kicad_env(out.parent), capture_output=True, text=True)
    if result.returncode != 0 or not out.exists():
        raise RuntimeError(f"kicad-cli {' '.join(args[:2])} failed ({result.returncode}): "
                           f"{(result.stdout + result.stderr).strip()[-2000:]}")
    # KiCad logs some problems (missing data files, libraries) as errors yet still exports.
    errors = [line for line in result.stderr.splitlines() if "Error:" in line]
    if errors:
        raise RuntimeError(f"kicad-cli {' '.join(args[:2])} reported errors:\n" + "\n".join(errors))
    return out.read_text()


def check(cli: list[str]) -> None:
    """Raise unless `cli sch export netlist` reproduces the expected nets and `cli sch erc` the expected
    errors (ERC also needs the cvpcb kiface)."""
    with tempfile.TemporaryDirectory() as tmp:
        net, erc = Path(tmp) / "smoke.net", Path(tmp) / "smoke-erc.json"
        nets = netlist_nets(_run(cli, ["sch", "export", "netlist", "-o", str(net), str(SCHEMATIC)], net))
        errors = erc_errors(_run(cli, ["sch", "erc", "--format", "json", "--severity-all", "-o", str(erc),
                                       str(SCHEMATIC)], erc))
    if nets != EXPECTED:
        raise RuntimeError(f"unexpected nets {nets}, wanted {EXPECTED}")
    if errors != EXPECTED_ERC:
        raise RuntimeError(f"unexpected ERC errors {errors}, wanted {EXPECTED_ERC}")
