"""Prove a bundle works: export the netlist of a known RC filter and compare KiCad's nets."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

SCHEMATIC = Path(__file__).parent / "smoke" / "rc_filter.kicad_sch"
EXPECTED = [["C1.1"], ["C1.2", "R1.2"], ["R1.1"]]
# Where KiCad writes config, documents and caches; point them at a private dir.
KICAD_HOMES = ("KICAD_CONFIG_HOME", "KICAD_DOCUMENTS_HOME", "KICAD_CACHE_HOME")
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
            stack[-1].append(tok[1:-1] if tok.startswith('"') else tok)
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


def kicad_env(home: Path) -> dict[str, str]:
    """Keep KiCad's config, document and cache dirs out of the user's home."""
    return {**os.environ, **{k: str(home / k) for k in KICAD_HOMES}}


def check(cli: list[str]) -> None:
    """Raise unless `cli sch export netlist` reproduces the expected nets."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "smoke.net"
        result = subprocess.run([*cli, "sch", "export", "netlist", "-o", str(out), str(SCHEMATIC)],
                                env=kicad_env(Path(tmp)), capture_output=True, text=True)
        if result.returncode != 0 or not out.exists():
            raise RuntimeError(f"kicad-cli failed ({result.returncode}): {result.stderr.strip()[-2000:]}")
        # KiCad logs some problems (missing data files, libraries) as errors yet still exports.
        errors = [line for line in result.stderr.splitlines() if "Error:" in line]
        if errors:
            raise RuntimeError("kicad-cli reported errors:\n" + "\n".join(errors))
        nets = netlist_nets(out.read_text())
    if nets != EXPECTED:
        raise RuntimeError(f"unexpected nets {nets}, wanted {EXPECTED}")
