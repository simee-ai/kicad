"""Read nets straight from an Eagle schematic's XML, as Eagle itself connects them.

An oracle for the import tests that doesn't go through KiCad: each <pinref> on a net becomes the
pads its pin connects to in the part's device (<connect gate pin pad>), named "PART.PAD" like
KiCad's netlist. Components are the parts whose device has a package (a footprint), pins or not;
supply and frame symbols have none. Parts are named as KiCad's importer renames them
(`kicad_reference`), and the pads of one pin are a net even when the pin is on none, as in KiCad.
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


def kicad_reference(part: str) -> str:
    """The reference KiCad's Eagle importer gives `part` (SCH_IO_EAGLE::loadInstance): it must end
    with a digit and start with something else."""
    ref = part if part[-1:].isdigit() else part + "0"
    return "UNK" + ref if ref[0].isdigit() else ref


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
        components[kicad_reference(name)] = part.get("value") or part.get("deviceset") + part.get("device")
        for c in dev.iter("connect"):
            pads[(name, c.get("gate"), c.get("pin"))] = c.get("pad").split()
    by_name: dict[str, set[str]] = {}
    on_a_net = set()
    for sheet_net in root.iter("net"):
        nodes = by_name.setdefault(sheet_net.get("name"), set())
        for ref in sheet_net.iter("pinref"):
            key = (ref.get("part"), ref.get("gate"), ref.get("pin"))
            on_a_net.add(key)
            nodes.update(f"{kicad_reference(key[0])}.{pad}" for pad in pads.get(key, []))
    pin_nets = [{f"{kicad_reference(k[0])}.{pad}" for pad in p} for k, p in pads.items() if k not in on_a_net]
    nets = sorted(sorted(n) for n in [*by_name.values(), *pin_nets] if len(n) > 1)
    return components, nets
