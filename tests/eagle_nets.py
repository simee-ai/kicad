"""Read nets straight from an Eagle schematic's XML, as Eagle itself connects them.

An oracle for the import tests that doesn't go through KiCad: each <pinref> on a net becomes the
pads its pin connects to in the part's device (<connect gate pin pad>), named "PART.PAD" like
KiCad's netlist. Components are the parts whose device has a package (a footprint), pins or not;
supply and frame symbols have none.
"""

import xml.etree.ElementTree as ET
from pathlib import Path


def _devices(root: ET.Element) -> dict[tuple[str, str, str], ET.Element]:
    devices = {}
    for lib in root.iter("library"):
        for dset in lib.iter("deviceset"):
            for dev in dset.iter("device"):
                devices[(lib.get("name"), dset.get("name"), dev.get("name"))] = dev
    return devices


def eagle_nets(path: Path) -> tuple[dict[str, str], list[list[str]]]:
    """Components (ref -> value) with a footprint, and nets with two or more nodes, sorted."""
    root = ET.parse(path).getroot()
    devices = _devices(root)
    pads: dict[tuple[str, str, str], list[str]] = {}
    components = {}
    for part in root.iter("part"):
        dev = devices[(part.get("library"), part.get("deviceset"), part.get("device"))]
        if dev.get("package") is None:
            continue
        name = part.get("name")
        components[name] = part.get("value") or part.get("deviceset") + part.get("device")
        for c in dev.iter("connect"):
            pads[(name, c.get("gate"), c.get("pin"))] = c.get("pad").split()
    by_name: dict[str, set[str]] = {}
    for sheet_net in root.iter("net"):
        nodes = by_name.setdefault(sheet_net.get("name"), set())
        for ref in sheet_net.iter("pinref"):
            key = (ref.get("part"), ref.get("gate"), ref.get("pin"))
            nodes.update(f"{key[0]}.{pad}" for pad in pads.get(key, []))
    nets = sorted(sorted(n) for n in by_name.values() if len(n) > 1)
    return components, nets
