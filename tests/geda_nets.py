"""Read nets from a netlist gEDA's own netlister wrote (`lepton-netlist -g PCB`, or gnetlist's).

The oracle for a gEDA fixture: each line is a net name, a tab and its pins as `REFDES-PIN`, a long
line continued with a trailing backslash. Nets come back as sorted "REF.PIN" lists, as KiCad's
netlist names them, leaving out nets of a single pin as the import check does.
"""

from pathlib import Path


def geda_nets(path: Path) -> list[list[str]]:
    nets = []
    for line in path.read_text().replace("\\\n", " ").splitlines():
        pins = sorted(".".join(p.rsplit("-", 1)) for p in line.split()[1:])
        if len(pins) > 1:
            nets.append(pins)
    return sorted(nets)
