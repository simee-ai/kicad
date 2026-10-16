"""Read EasyEDA Pro's own netlist from a project file (.epro, or the older .zip), without KiCad.

The oracle for an EasyEDA Pro fixture: a board's PCB document (PCB/<id>.epcb, JSON lines) keeps the
net of every pad as EasyEDA Pro synced it from the schematic (`PAD_NET` lines: part, pad number, net),
each part's `Designator` (`ATTR` lines), and free pads (`PAD` lines, such as a mounting hole on GND),
which are on the board only. Nets come back as sorted "REF.PAD" lists, as KiCad names them. A project
can hold several boards (a schematic + PCB pair each); kicad-cli imports the first by name
(simee/10.0.6's ChooseFirstProject, #29), and so does this.
"""

import json
import zipfile
from collections import defaultdict
from pathlib import Path

MM_PER_MIL = 0.0254
OUTLINE_LAYER = 11


def _lines(text: str) -> list[list]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def default_board(project: dict) -> tuple[str, str, str]:
    """(board name, schematic id, PCB id) of the board kicad-cli imports from a project.json."""
    name = min(project["boards"])
    board = project["boards"][name]
    return name, board["schematic"], board["pcb"]


def _outline_points(path: list) -> list[tuple[float, float]]:
    """The vertices of a POLY path: a start point, then "L" x y [x y ...] and "ARC"/"CARC" angle x y."""
    points, i = [(path[0], path[1])], 2
    while i < len(path):
        op = path[i]
        if op == "L":
            i += 1
            while i + 1 < len(path) and not isinstance(path[i], str):
                points.append((path[i], path[i + 1]))
                i += 2
        elif op in ("ARC", "CARC"):
            points.append((path[i + 2], path[i + 3]))
            i += 4
        else:
            raise ValueError(f"unknown outline path element {op!r}")
    return points


def _sorted(nets: dict[str, set[str]]) -> list[list[str]]:
    return sorted(sorted(pads) for pads in nets.values())


def _pcb(path: Path) -> tuple[dict[str, str], dict[str, set[str]], dict[str, set[str]], tuple[float, float]]:
    """The default board's designators (part id -> designator), its parts' pads by net, its free pads by
    net (named as KiCad imports them: a footprint with no reference) and its outline size (mm, the extent
    of its vertices: right for the straight sides and corner arcs boards have)."""
    with zipfile.ZipFile(path) as zf:
        _, _, pcb = default_board(json.loads(zf.read("project.json")))
        lines = _lines(zf.read(f"PCB/{pcb}.epcb").decode())
    designators = {line[3]: line[8] for line in lines if line[0] == "ATTR" and line[7] == "Designator"}
    part_pads, free_pads = defaultdict(set), defaultdict(set)
    for line in lines:
        if line[0] == "PAD_NET" and line[3]:
            part_pads[line[3]].add(f"{designators[line[1]]}.{line[2]}")
        elif line[0] == "PAD" and line[3]:
            free_pads[line[3]].add(f".{line[5]}")
    xs, ys = zip(*(p for line in lines if line[0] == "POLY" and line[4] == OUTLINE_LAYER
                   for p in _outline_points(line[6])))
    size = (round((max(xs) - min(xs)) * MM_PER_MIL, 2), round((max(ys) - min(ys)) * MM_PER_MIL, 2))
    return designators, part_pads, free_pads, size


def easyedapro_board(path: Path) -> tuple[set[str], tuple[float, float], list[list[str]]]:
    """The default board's part designators, outline size (mm) and every net with a pad, free pads and
    single-pad nets too."""
    designators, part_pads, free_pads, size = _pcb(path)
    nets = defaultdict(set, {net: set(pads) for net, pads in part_pads.items()})
    for net, pads in free_pads.items():
        nets[net] |= pads
    return set(designators.values()), size, _sorted(nets)


def easyedapro_nets(path: Path) -> list[list[str]]:
    """The default board's nets as its schematic has them: its parts' pads, two or more."""
    return [net for net in _sorted(_pcb(path)[1]) if len(net) > 1]
