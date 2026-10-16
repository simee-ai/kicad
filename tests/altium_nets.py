"""Read the nets of an Altium board (.PcbDoc) from its own records, without KiCad.

The oracle for an Altium fixture: the board holds the netlist Altium compiled from the whole
project, every sheet's connections resolved under the project's net identifier scope. Each pad of
Pads6 names its net (Nets6) and component (Components6) by index; pads come back grouped as sorted
"REF.PAD" lists, as KiCad's netlist names them, leaving out nets of a single pad as the import
check does. Layout from KiCad's own Altium board reader (pcbnew/pcb_io/altium/altium_parser_pcb.cpp).
"""

import struct
from pathlib import Path

import olefile

NO_INDEX = 0xFFFF  # a pad on no net, or one of no component
PAD_RECORD = 2


def properties(data: bytes) -> list[dict[str, str]]:
    """The `|KEY=value|...` records of a property stream (Nets6, Components6), keys upper-cased."""
    records, i = [], 0
    while i + 4 <= len(data):
        size = struct.unpack_from("<I", data, i)[0] & 0x00FFFFFF
        text = data[i + 4:i + 4 + size].rstrip(b"\0").decode("latin-1")
        i += 4 + size
        records.append({k.upper(): v for k, _, v in (kv.partition("=") for kv in text.split("|") if "=" in kv)})
    return records


def pads(data: bytes) -> list[tuple[str, int, int]]:
    """(name, net index, component index) of each pad of a Pads6 stream."""
    found, i = [], 0
    while i < len(data):
        if data[i] != PAD_RECORD:
            raise ValueError(f"Pads6: record type {data[i]} at {i}")
        i += 1
        subrecords = []
        for _ in range(5):
            size = struct.unpack_from("<I", data, i)[0]
            subrecords.append(data[i + 4:i + 4 + size])
            i += 4 + size
        name, main = subrecords[0][1:1 + subrecords[0][0]].decode("latin-1"), subrecords[4]
        found.append((name, struct.unpack_from("<H", main, 3)[0], struct.unpack_from("<H", main, 7)[0]))
        # A sixth subrecord (per-layer sizes) follows when the pad has one
        if i < len(data) and data[i] != PAD_RECORD:
            i += 4 + struct.unpack_from("<I", data, i)[0]
    return found


def altium_nets(path: Path) -> list[list[str]]:
    with olefile.OleFileIO(str(path)) as ole:
        nets = [r.get("NAME", "") for r in properties(ole.openstream("Nets6/Data").read())]
        refs = [r.get("SOURCEDESIGNATOR", "") for r in properties(ole.openstream("Components6/Data").read())]
        grouped: dict[int, set[str]] = {}
        for name, net, component in pads(ole.openstream("Pads6/Data").read()):
            if net != NO_INDEX and component != NO_INDEX:
                grouped.setdefault(net, set()).add(f"{refs[component]}.{name}")
    return sorted(sorted(p) for p in grouped.values() if len(p) > 1)
