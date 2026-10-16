"""Write utf8-attributes.epro: a tiny EasyEDA Pro project whose parts take their values from device
attributes with Chinese names (simee-ai/simee-kicad#35).

R1's Value is `={阻值}` and R2's `={阻值} {精度}`: EasyEDA Pro fills each `{name}` from the part's device
attributes, so they read 10k and 4.7k 1%. KiCad's importer once built the map of those attributes with
wxString's locale-dependent std::string constructor, so in the C locale every Chinese name became "" and
none of them was found. R1.2 and R2.1 are joined by a wire on net MID.

    uv run --no-project python make_epro.py
"""

import json
import zipfile
from pathlib import Path

SCHEMATIC, SHEET, PCB, SYMBOL = "5c0e" * 8, "5e1e" * 8, "9cb0" * 8, "5b10" * 8
DEVICES = {
    "d1" * 16: {"类型": "贴片电阻", "阻值": "10k"},
    "d2" * 16: {"阻值": "4.7k", "精度": "1%"},
}
# (part id, designator, Value, device, x)
PARTS = [("e1", "R1", "={阻值}", "d1" * 16, 100), ("e2", "R2", "={阻值} {精度}", "d2" * 16, 200)]


def lines(rows: list[list]) -> str:
    # no newline at the end: KiCad reads an empty last line as a null record
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)


def project() -> dict:
    return {
        "schematics": {SCHEMATIC: {"name": "Main", "sheets": [{"name": "Main", "id": 1, "uuid": SHEET}]}},
        "pcbs": {PCB: "PCB"},
        "panels": {},
        "symbols": {SYMBOL: {"source": "", "desc": "", "tags": {}, "custom_tags": "", "title": "RES",
                             "version": "1", "type": 2}},
        "footprints": {},
        "devices": {
            device: {"title": f"RES {i}", "attributes": {"Symbol": SYMBOL, "Designator": "R?", "Name": "", **attrs},
                     "description": "", "tags": {}, "images": [], "source": "", "version": "", "custom_tags": ""}
            for i, (device, attrs) in enumerate(DEVICES.items(), 1)},
        "boards": {"Main": {"schematic": SCHEMATIC, "pcb": PCB}},
        "config": {"title": "utf8 attributes", "defaultSheet": SHEET},
    }


def symbol() -> str:
    """A resistor: a box with pin 1 at x -20 and pin 2 at x 20."""
    return lines([
        ["DOCTYPE", "SYMBOL", "1.1"],
        ["HEAD", {"originX": 0, "originY": 0, "version": "1.6.24", "symbolType": 2}],
        ["PART", "RES.1", {"BBOX": [-10, 4, 10, -4]}],
        ["FONTSTYLE", "st1", None, None, None, None, None, None, None, None, None, 0],
        ["LINESTYLE", "st2", None, None, None, None],
        ["RECT", "e3", -10, 4, 10, -4, 0, 0, 0, "st2", 0],
        *[row for pin, (number, x, rotation) in enumerate([("1", -20, 0), ("2", 20, 180)])
          for row in (["PIN", f"p{pin}", 1, 1, x, 0, 10, rotation, None, 0, 0],
                      ["ATTR", f"p{pin}n", f"p{pin}", "NAME", number, 0, 0, None, None, 0, "st1", 0],
                      ["ATTR", f"p{pin}u", f"p{pin}", "NUMBER", number, 0, 0, None, None, 0, "st1", 0])],
    ])


def sheet() -> str:
    rows = [["DOCTYPE", "SCH", "1.1"], ["HEAD", {"originX": 0, "originY": 0, "version": "1.9.29", "maxId": 99}],
            ["FONTSTYLE", "st1", None, None, None, None, None, None, None, None, None, 0]]
    for part, designator, value, device, x in PARTS:
        rows += [["COMPONENT", part, "RES.1", x, 0, 0, 0, {}, 0],
                 ["ATTR", f"{part}s", part, "Symbol", SYMBOL, 0, 0, None, None, 0, "st1", 0],
                 ["ATTR", f"{part}d", part, "Designator", designator, 0, 1, x, -10, 0, "st1", 0],
                 ["ATTR", f"{part}v", part, "Value", value, 0, 1, x, 10, 0, "st1", 0],
                 ["ATTR", f"{part}e", part, "Device", device, 0, 0, None, None, 0, "st1", 0],
                 ["ATTR", f"{part}n", part, "Name", "", 0, 0, None, None, 0, "st1", 0]]
    rows += [["WIRE", "w1", [[120, 0, 180, 0]], "st1", 0],
             ["ATTR", "w1n", "w1", "NET", "MID", 0, 0, 150, 0, 0, "st1", 0]]
    return lines(rows)


def pcb() -> str:
    """The board EasyEDA Pro syncs from the schematic: the oracle tests/easyedapro_nets.py reads."""
    rows = [["DOCTYPE", "PCB", "1.7"]]
    for part, designator, _, _, _ in PARTS:
        rows.append(["ATTR", f"{part}d", 0, part, 3, 0, 0, "Designator", designator, 0, 1])
    rows += [["PAD_NET", "e1", "1", "", "n1"], ["PAD_NET", "e1", "2", "MID", "n2"],
             ["PAD_NET", "e2", "1", "MID", "n3"], ["PAD_NET", "e2", "2", "", "n4"],
             ["POLY", "o1", 0, "", 11, 10, [0, 0, "L", 1000, 0, 1000, 500, 0, 500, 0, 0], 0]]
    return lines(rows)


def write(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in [("project.json", json.dumps(project(), ensure_ascii=False)),
                           (f"SYMBOL/{SYMBOL}.esym", symbol()),
                           (f"SHEET/{SCHEMATIC}/1.esch", sheet()),
                           (f"PCB/{PCB}.epcb", pcb())]:
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8"))


if __name__ == "__main__":
    write(Path(__file__).with_name("utf8-attributes.epro"))
