import importlib.util
import json
import os
import shlex
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from altium_nets import altium_nets, pads, properties
from eagle_nets import eagle_nets, kicad_reference
from easyedapro_nets import easyedapro_board, easyedapro_nets
from geda_nets import geda_nets
from kicad_bundle import sch_import
from kicad_bundle.smoke import kicad_env, netlist_components

FIXTURES = sch_import.fixtures()
IDS = [f.name for f in FIXTURES]
# A kicad-cli with `sch import` (built from a simee/<version> branch); the import tests skip without it.
KICAD_CLI = os.environ.get("KICAD_CLI")


def test_netlist_components_reads_kicadsexpr():
    text = """(export (version "E")
      (components
        (comp (ref "R1") (value "10K") (footprint "R_0603"))
        (comp (ref "U1") (value "BME280"))
        (comp (ref "U$5") (value "FIDUCIAL\\"\\"")))
      (nets))"""
    assert netlist_components(text) == {"R1": "10K", "U1": "BME280", "U$5": 'FIDUCIAL""'}


def test_fixtures_cover_every_format_simee_db_needs():
    assert {f.format for f in FIXTURES} >= {"eagle", "altium", "easyeda"}
    assert len([f for f in FIXTURES if f.format == "eagle"]) >= 2


def test_fixtures_cover_a_multi_sheet_eagle_schematic():
    # Each Eagle sheet is a top-level sheet in KiCad, and the import has to keep them all (#17)
    sheets = [len(ET.parse(f.source).getroot().findall(".//sheets/sheet")) for f in FIXTURES if f.format == "eagle"]
    assert max(sheets) >= 2


def test_stray_files_are_the_hidden_ones(tmp_path):
    for name in (".kicad_sch", "imported.kicad_sch", "imported-eagle-import.kicad_sym", "sym-lib-table"):
        (tmp_path / name).touch()
    assert sch_import.stray_files(tmp_path) == [".kicad_sch"]


def test_written_beside_the_source_are_new_files_outside_the_output(tmp_path):
    # EasyEDA Pro's importer saved its symbol library next to the input, not the output (#29)
    src, out = tmp_path, tmp_path / "kicad" / "sub"
    out.mkdir(parents=True)
    (src / "board.epro").touch()
    before = sch_import.source_files(src, out)
    (src / "board-easyedapro.kicad_sym").touch()
    (out / "imported.kicad_sch").touch()
    assert sch_import.written_beside(src, out, before) == ["board-easyedapro.kicad_sym"]
    # with the source inside the output, everything new under the source counts
    assert sch_import.written_beside(out, tmp_path, set()) == ["imported.kicad_sch"]


def test_sheets_written_beside_the_source_are_listed(tmp_path):
    # The Windows bundle wrote a multi-sheet EasyEDA Pro import's sheets beside the source (#36)
    (tmp_path / "board.epro").touch()
    before = sch_import.source_files(tmp_path, tmp_path / "out")
    (tmp_path / "1_POWER.kicad_sch").touch()
    assert sch_import.written_beside(tmp_path, tmp_path / "out", before) == ["1_POWER.kicad_sch"]


def sheet(folder: Path, name: str, *sheetfiles: str) -> None:
    (folder / name).parent.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("(kicad_sch\n" + "".join(
        f'\t(sheet\n\t\t(property "Sheetname" "s{i}")\n\t\t(property "Sheetfile" "{f}")\n\t)\n'
        for i, f in enumerate(sheetfiles)) + ")\n", encoding="utf-8")


def test_sheets_named_outside_the_output_are_listed(tmp_path):
    # On Windows a %TEMP% with 8.3 short names (RUNNER~1) had the import name its sheets by ../../.. paths,
    # into the source's folder or nowhere near either; the netlist still matched (#36)
    out = tmp_path / "out"
    sheet(out, "imported.kicad_sch", "1_POWER.kicad_sch", "sub/2_DRAM.kicad_sch")
    sheet(out, "sub/2_DRAM.kicad_sch", "../3_IO.kicad_sch")
    assert sch_import.sheets_outside(out) == []
    sheet(out, "imported.kicad_sch", "1_POWER.kicad_sch", "../src/4_PREP.kicad_sch",
          "../../../../RUNNER~1/AppData/Local/Temp/job/src/5_X.kicad_sch", str(tmp_path / "elsewhere.kicad_sch"))
    assert sch_import.sheets_outside(out) == [
        "imported.kicad_sch names ../src/4_PREP.kicad_sch",
        "imported.kicad_sch names ../../../../RUNNER~1/AppData/Local/Temp/job/src/5_X.kicad_sch",
        f"imported.kicad_sch names {tmp_path / 'elsewhere.kicad_sch'}"]


def test_check_rejects_an_import_that_leaves_stray_files(monkeypatch):
    fixture = FIXTURES[0]
    monkeypatch.setattr(sch_import, "imported", lambda cli, f, layout: (f.components, f.nets, [".kicad_sch"]))
    with pytest.raises(RuntimeError, match=r"stray files.*\.kicad_sch"):
        sch_import.check(["kicad-cli"], fixture)


def test_layouts_nest_the_output_in_the_source_folder_and_the_other_way_round():
    # A multi-sheet import put its sheets in <out>/<out relative to the source's folder> (#18)
    pairs = [tuple(Path(d) for d in pair) for pair in sch_import.LAYOUTS.values()]
    assert any(out != src and out.is_relative_to(src) for src, out in pairs)
    assert any(src != out and src.is_relative_to(out) for src, out in pairs)
    assert any(not src.is_relative_to(out) and not out.is_relative_to(src) for src, out in pairs)


def test_check_imports_in_every_layout_and_names_the_one_that_failed(monkeypatch):
    fixture = FIXTURES[0]
    seen = []

    def imported(cli, f, layout):
        seen.append(layout)
        return ({}, f.nets, []) if layout == "output inside source" else (f.components, f.nets, [])

    monkeypatch.setattr(sch_import, "imported", imported)
    with pytest.raises(RuntimeError, match=r"^[^;]*output inside source: components differ[^;]*$"):
        sch_import.check(["kicad-cli"], fixture)
    assert seen == list(sch_import.LAYOUTS)


@pytest.mark.parametrize("fixture", FIXTURES, ids=IDS)
def test_fixture_is_redistributable(fixture):
    assert fixture.source.is_file()
    for key in ("origin", "licence", "licence_file", "checked"):
        assert fixture.meta.get(key), f"{fixture.name}: fixture.json needs {key}"
    assert (fixture.source.parent / fixture.meta["licence_file"]).is_file()


TINY_EAGLE = """<eagle><drawing><schematic>
  <libraries><library name="l"><devicesets>
    <deviceset name="USB"><devices><device name="" package="P"><connects>
      <connect gate="G" pin="DP" pad="A6 B6"/><connect gate="G" pin="VBUS" pad="A4"/>
    </connects></device></devices></deviceset>
    <deviceset name="R"><devices><device name="" package="P"><connects>
      <connect gate="G" pin="1" pad="1"/><connect gate="G" pin="2" pad="2"/>
    </connects></device></devices></deviceset>
  </devicesets></library></libraries>
  <parts>
    <part name="J1" library="l" deviceset="USB" device="" value="USB-C"/>
    <part name="3V3_QWIIC" library="l" deviceset="R" device="" value="JUMPER"/>
  </parts>
  <sheets><sheet><nets><net name="V"><segment>
    <pinref part="J1" gate="G" pin="VBUS"/><pinref part="3V3_QWIIC" gate="G" pin="1"/>
  </segment></net></nets></sheet></sheets>
</schematic></drawing></eagle>"""


def test_kicad_reference_is_what_kicads_eagle_importer_names_a_part():
    assert kicad_reference("R1") == "R1"
    assert kicad_reference("I2C") == "I2C0"
    assert kicad_reference("3V3_QWIIC") == "UNK3V3_QWIIC0"


def test_eagle_nets_join_the_pads_of_one_pin_and_name_parts_as_kicad_does(tmp_path):
    sch = tmp_path / "tiny.sch"
    sch.write_text(TINY_EAGLE)
    components, nets = eagle_nets(sch)
    assert components == {"J1": "USB-C", "UNK3V3_QWIIC0": "JUMPER"}
    # J1's DP pin is on no net, but its two pads are still one node, as KiCad's import has them
    assert nets == [["J1.A4", "UNK3V3_QWIIC0.1"], ["J1.A6", "J1.B6"]]


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f.format == "eagle"],
                         ids=[f.name for f in FIXTURES if f.format == "eagle"])
def test_eagle_expectations_are_what_eagle_connects(fixture):
    assert eagle_nets(fixture.source) == (fixture.components, fixture.nets)


def test_fixtures_cover_a_geda_schematic_with_its_own_symbols():
    geda = [f for f in FIXTURES if f.format == "geda"]
    assert geda and any((f.source.parent / "gafrc").is_file() for f in geda)


def test_a_format_kicad_cli_has_no_hint_for_is_found_by_its_content():
    # `sch import --format` names no gEDA importer, though auto finds it (simee-core#456)
    assert sch_import.format_hint("eagle") == "eagle"
    assert sch_import.format_hint("geda") == "auto"


def test_geda_nets_read_a_lepton_pcb_netlist(tmp_path):
    net = tmp_path / "x.net"
    net.write_text("unnamed_net2\tR1-2 \nGND\tC1-1 R1-1 U1A-4 \\\n U1-4 \n5V\tU1A-8 J9-1 \n")
    assert geda_nets(net) == [["C1.1", "R1.1", "U1.4", "U1A.4"], ["J9.1", "U1A.8"]]


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f.format == "geda"],
                         ids=[f.name for f in FIXTURES if f.format == "geda"])
def test_geda_expectations_are_what_lepton_netlists(fixture):
    assert fixture.nets == geda_nets(fixture.source.parent / fixture.meta["netlist"])


def test_fixtures_cover_a_multi_sheet_altium_project():
    # A .PrjPcb opens every sheet it lists, and Altium's net identifier scope joins them (#33)
    projects = [f for f in FIXTURES if f.source.suffix.lower() == ".prjpcb"]
    assert projects
    assert max(len(list(f.source.parent.glob("*.SchDoc"))) for f in projects) >= 2


def _subrecord(data: bytes) -> bytes:
    return len(data).to_bytes(4, "little") + data


def _pad(name: str, net: int, component: int) -> bytes:
    main = bytes([1, 0, 0]) + net.to_bytes(2, "little") + b"\0\0" + component.to_bytes(2, "little")
    return (bytes([2]) + _subrecord(bytes([len(name)]) + name.encode()) + _subrecord(b"")
            + _subrecord(b"") + _subrecord(b"") + _subrecord(main.ljust(110, b"\0")))


def test_altium_board_records_name_each_pads_net_and_component():
    record = b"|NAME=GND|ID=1\0"
    assert properties(len(record).to_bytes(4, "little") + record) == [{"NAME": "GND", "ID": "1"}]
    # The second pad has a sixth subrecord, the third is on no net
    data = _pad("1", 0, 3) + _pad("A5", 1, 0) + _subrecord(b"sizes") + _pad("2", 0xFFFF, 0)
    assert pads(data) == [("1", 0, 3), ("A5", 1, 0), ("2", 0xFFFF, 0)]


# The board Altium compiled from a fixture's project: its nets are the oracle for the schematic's
ALTIUM_BOARDS = [f for f in FIXTURES if "altium_board" in f.meta]


@pytest.mark.parametrize("fixture", ALTIUM_BOARDS, ids=[f.name for f in ALTIUM_BOARDS])
def test_altium_expectations_are_what_its_board_connects(fixture):
    assert fixture.nets == altium_nets(fixture.source.parent / fixture.meta["altium_board"])


def write_epro(path, boards, pcb_lines):
    """A tiny EasyEDA Pro project: project.json naming `boards` (name -> PCB id), one PCB document."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("project.json", json.dumps({"boards": {
            name: {"schematic": f"s-{pcb}", "pcb": pcb} for name, pcb in boards.items()}}))
        for pcb in boards.values():
            zf.writestr(f"PCB/{pcb}.epcb", "\n".join(json.dumps(line) for line in pcb_lines(pcb)))


def tiny_pcb(pcb):
    first = pcb == "p1"
    return [
        ["DOCTYPE", "PCB", "1.7"],
        ["ATTR", "e1e2", 0, "e1", 3, 0, 0, "Designator", "U1" if first else "Q9", 0, 1],
        ["ATTR", "e1e3", 0, "e1", 3, None, None, "Footprint", "abc", 0, 0],
        ["ATTR", "e4e5", 0, "e4", 3, 0, 0, "Designator", "C1", 0, 1],
        ["PAD_NET", "e1", "A1", "GND", "e6"],
        ["PAD_NET", "e4", "2", "GND", "e7"],
        ["PAD_NET", "e4", "1", "VCC", "e8"],
        ["PAD_NET", "e1", "B2", "", "e9"],
        # a free pad (a mounting hole), and one on no net
        ["PAD", "e12", 0, "GND", 12, "1", 50, 50, 0, ["ROUND", 118, 118], ["ELLIPSE", 196, 196], [], 0],
        ["PAD", "e13", 0, "", 12, "1", 950, 450, 0, ["ROUND", 118, 118], ["ELLIPSE", 196, 196], [], 0],
        # a rounded rectangle 1000 x 500 mil, as EasyEDA Pro draws board outlines
        ["POLY", "e10", 0, "", 11, 10, [100, 0, "L", 900, 0, "ARC", 90, 1000, 100, "L", 1000, 400,
                                        "ARC", 90, 900, 500, "L", 100, 500, 0, 400, "L", 0, 100, "ARC", 90, 100, 0], 0],
        ["POLY", "e11", 0, "", 3, 10, [0, 0, "L", 5000, 5000], 0],
    ]


def test_easyedapro_board_reads_the_first_boards_pads_and_outline(tmp_path):
    epro = tmp_path / "tiny.epro"
    # boards in name order, as kicad-cli takes the first (#29): "A main" before "B other"
    write_epro(epro, {"B other": "p2", "A main": "p1"}, tiny_pcb)
    designators, size, nets = easyedapro_board(epro)
    assert designators == {"U1", "C1"}
    # silkscreen doesn't count; a pad on net "" is on none; a single-pad net is a net of the board
    assert size == (25.4, 12.7)
    # a free pad is a footprint with no reference in KiCad, and on the board only
    assert nets == [[".1", "C1.2", "U1.A1"], ["C1.1"]]
    assert easyedapro_nets(epro) == [["C1.2", "U1.A1"]]


def test_fixtures_cover_an_easyedapro_project_holding_several_boards():
    # kicad-cli crashed on such a file, asking a chooser nobody had registered (#29)
    epros = [f for f in FIXTURES if f.format == "easyedapro"]
    assert any(len(json.loads(zipfile.ZipFile(f.source).read("project.json"))["boards"]) > 1 for f in epros)


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f.format == "easyedapro" and not f.project],
                         ids=[f.name for f in FIXTURES if f.format == "easyedapro" and not f.project])
def test_easyedapro_expectations_are_what_easyeda_pro_connects(fixture):
    designators, _, _ = easyedapro_board(fixture.source)
    assert fixture.nets == easyedapro_nets(fixture.source)
    assert set(fixture.components) == designators


def test_utf8_attributes_epro_is_what_its_script_writes(tmp_path):
    # Its parts' values come from device attributes with Chinese names, which the C locale lost (#35)
    folder = sch_import.FIXTURES / "easyedapro-utf8-attributes"
    spec = importlib.util.spec_from_file_location("make_epro", folder / "make_epro.py")
    make_epro = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_epro)
    make_epro.write(tmp_path / "made.epro")
    assert (tmp_path / "made.epro").read_bytes() == (folder / "utf8-attributes.epro").read_bytes()
    attributes = json.loads(zipfile.ZipFile(folder / "utf8-attributes.epro").read("project.json"))["devices"]
    assert any(not key.isascii() for device in attributes.values() for key in device["attributes"])


@pytest.mark.parametrize("fixture", FIXTURES, ids=IDS)
def test_fixture_nets_are_sorted_and_disjoint(fixture):
    assert fixture.nets == sorted(sorted(n) for n in fixture.nets)
    nodes = [node for net in fixture.nets for node in net]
    assert len(nodes) == len(set(nodes))
    assert {node.split(".")[0] for node in nodes} <= set(fixture.components)


@pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
@pytest.mark.parametrize("fixture", FIXTURES, ids=IDS)
def test_import_matches_source_tool(fixture):
    sch_import.check(shlex.split(KICAD_CLI), fixture)


@pytest.mark.parametrize("fixture", [f for f in FIXTURES if f.format == "ltspice"],
                         ids=[f.name for f in FIXTURES if f.format == "ltspice"])
def test_ltspice_fixtures_bring_every_symbol_they_place(fixture):
    # KiCad takes LTspice's own symbols from its install, if any (none on CI), after the schematic's folder
    symbols = {line.split()[1].split("\\")[-1].lower() for line in fixture.source.read_text().splitlines()
               if line.startswith("SYMBOL ")}
    assert symbols and all((fixture.source.parent / f"{s}.asy").is_file() for s in symbols)


DETECTIONS = sch_import.detections()
DETECTION_IDS = [d.label for d in DETECTIONS]


def test_every_fixture_format_has_kicads_importer_name():
    assert {f.format for f in FIXTURES} <= set(sch_import.FORMAT_NAMES)


def test_detections_cover_every_fixture_and_the_asc_files_ltspice_must_leave_alone():
    # `.asc` is LTspice's extension and PADS's (Logic schematics, Layout boards): #31
    cases = {(d.name, d.format, d.utf16) for d in DETECTIONS}
    assert {(f.source.name, f.format, False) for f in FIXTURES} <= cases
    assert ("multigate_schematic.asc", "pads", False) in cases
    assert ("synthetic_filled_copper.asc", None, False) in cases
    # LTspice XVII saves UTF-16LE
    assert any(fmt == "ltspice" and utf16 for _, fmt, utf16 in cases)


def test_detection_files_are_redistributable():
    meta = json.loads((sch_import.DETECT / "detect.json").read_text())
    for key in ("origin", "licence", "licence_file"):
        assert meta.get(key), f"detect.json needs {key}"
    assert (sch_import.DETECT / meta["licence_file"]).is_file()
    for d in DETECTIONS:
        assert d.source.is_file()


def fake_cli(tmp_path, output: str, code: int = 0) -> list[str]:
    script = tmp_path / "kicad-cli"
    script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{output}\nEOF\nexit {code}\n")
    script.chmod(0o755)
    return [str(script)]


def test_detected_is_the_importer_kicad_cli_names(tmp_path):
    case = next(d for d in DETECTIONS if d.format == "pads")
    cli = fake_cli(tmp_path, "Importing '/x/mg.asc' using LTspice format...\nError: Extra data found on line 22")
    assert sch_import.detected(cli, case) == "LTspice"


def test_detected_is_none_when_no_importer_recognizes_the_file(tmp_path):
    case = next(d for d in DETECTIONS if d.format is None)
    cli = fake_cli(tmp_path, "No schematic importer recognizes the file format of '/x/b.asc'", code=6)
    assert sch_import.detected(cli, case) is None


def test_detected_refuses_output_it_cannot_read(tmp_path):
    with pytest.raises(RuntimeError, match="names no importer"):
        sch_import.detected(fake_cli(tmp_path, "Segmentation fault", code=139), DETECTIONS[0])


def test_utf16_detection_imports_the_source_re_encoded(tmp_path):
    case = next(d for d in DETECTIONS if d.utf16)
    script = tmp_path / "kicad-cli"
    # the input is the last argument; report its first bytes
    script.write_text('#!/bin/sh\nfor a; do f=$a; done\necho "using $(head -c 4 "$f" | xxd -p) format..."\n')
    script.chmod(0o755)
    assert sch_import.detected([str(script)], case) == case.source.read_bytes()[:2].decode().encode("utf-16-le").hex()


def test_check_detection_names_every_misdetected_file(monkeypatch):
    names = {d.label: "LTspice" for d in DETECTIONS}
    monkeypatch.setattr(sch_import, "detected", lambda cli, d: names[d.label])
    with pytest.raises(RuntimeError) as e:
        sch_import.check_detection(["kicad-cli"])
    assert "multigate_schematic.asc: imported as LTspice, wanted PADS Logic" in str(e.value)
    assert "synthetic_filled_copper.asc: imported as LTspice, wanted no importer" in str(e.value)
    assert not [d.label for d in DETECTIONS if d.format == "ltspice" and f"{d.label}:" in str(e.value)]


@pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
@pytest.mark.parametrize("case", DETECTIONS, ids=DETECTION_IDS)
def test_auto_import_picks_the_files_own_importer(case):
    assert sch_import.detected(shlex.split(KICAD_CLI), case) == sch_import.FORMAT_NAMES.get(case.format)


# Projects of a file holding several, picked by `--project` and listed by `--list-projects` (#45)
NAMED = [f for f in FIXTURES if f.project]


def test_fixtures_cover_a_project_named_that_isnt_the_first():
    assert any(f.format == "easyedapro" and f.project != f"board '{min(epro_boards(f.source))}'" for f in NAMED)


def epro_boards(source):
    return json.loads(zipfile.ZipFile(source).read("project.json"))["boards"]


@pytest.mark.parametrize("fixture", NAMED, ids=[f.name for f in NAMED])
def test_a_named_project_is_a_board_of_its_source(fixture):
    assert sch_import.board_name(fixture.project) in epro_boards(fixture.source)


@pytest.mark.parametrize("fixture", NAMED, ids=[f.name for f in NAMED])
def test_named_easyedapro_expectations_are_what_easyeda_pro_connects_on_that_board(fixture):
    board = sch_import.board_name(fixture.project)
    designators, size, nets = easyedapro_board(fixture.source, board)
    assert fixture.nets == easyedapro_nets(fixture.source, board)
    assert set(fixture.components) == designators
    assert (tuple(fixture.meta["board"]["size_mm"]), fixture.meta["board"]["nets"]) == (size, nets)


def test_board_name_is_the_name_of_a_board_only():
    assert sch_import.board_name("board 'A, b'") == "A, b"
    assert sch_import.board_name("schematic 'A'") is None
    assert sch_import.board_name(None) is None


def test_the_import_of_a_fixture_naming_a_project_passes_it(monkeypatch):
    calls = []

    def run(cli, args, home):
        calls.append(args)
        raise StopIteration

    monkeypatch.setattr(sch_import, "_run", run)
    with pytest.raises(StopIteration):
        sch_import.imported(["kicad-cli"], NAMED[0])
    assert calls[0][:2] == ["sch", "import"] and calls[0][-3:-1] == ["--project", NAMED[0].project]


def test_projects_are_the_project_lines_kicad_cli_lists(tmp_path):
    cli = fake_cli(tmp_path, "Importing '/x.epro' using EasyEDA (JLCEDA) Pro format...\n"
                             "The source holds 2 projects:\nProject: board 'A, b'\nProject: PCB '散热器'")
    assert sch_import.projects(cli, "sch", NAMED[0].source) == ["board 'A, b'", "PCB '散热器'"]


def test_projects_of_a_source_that_is_no_project_file_are_none(tmp_path):
    cli = fake_cli(tmp_path, "Importing '/x.brd' using EAGLE format...\nThe source holds one project")
    assert sch_import.projects(cli, "pcb", NAMED[0].source) == []


def test_projects_refuses_a_failed_listing(tmp_path):
    with pytest.raises(RuntimeError, match="list-projects"):
        sch_import.projects(fake_cli(tmp_path, "Segmentation fault", code=139), "sch", NAMED[0].source)


LISTED = [f for f in FIXTURES if "projects" in f.meta]
SEVERAL = next(f for f in LISTED if len(f.meta["projects"]["sch"]) > 1)


def test_fixtures_list_the_projects_of_a_file_holding_several():
    # sch import lists the boards and lone schematics, pcb import the boards and lone PCBs
    assert any(f.meta["projects"]["sch"] != f.meta["projects"]["pcb"] for f in LISTED)


@pytest.mark.parametrize("fixture", LISTED, ids=[f.name for f in LISTED])
def test_listed_projects_are_the_sources_own(fixture):
    project = json.loads(zipfile.ZipFile(fixture.source).read("project.json"))
    boards = [f"board '{name}'" for name in sorted(project["boards"])]
    paired = {b[k] for b in project["boards"].values() for k in ("schematic", "pcb")}
    # lone PCBs and schematics in id order, as KiCad's EasyEDA Pro importer offers them
    pcbs = [f"PCB '{v if isinstance(v, str) else v['title']}'" for k, v in sorted(project["pcbs"].items())
            if k not in paired]
    schematics = [f"schematic '{v['name']}'" for k, v in sorted(project["schematics"].items()) if k not in paired]
    assert fixture.meta["projects"] == {"sch": boards + schematics, "pcb": boards + pcbs}


@pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
@pytest.mark.parametrize("fixture", FIXTURES, ids=IDS)
def test_list_projects_names_each_project_of_the_source(fixture):
    sch_import.check_projects(shlex.split(KICAD_CLI), fixture)


@pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
@pytest.mark.parametrize("kind", ["sch", "pcb"])
def test_a_project_no_project_of_the_source_is_fails_naming_them(kind, tmp_path):
    fixture = SEVERAL
    result = subprocess.run([*shlex.split(KICAD_CLI), kind, "import", "--project", "board 'nope'",
                             "-o", str(tmp_path / f"out.kicad_{kind}"), str(fixture.source)],
                            env=kicad_env(tmp_path), capture_output=True, encoding="utf-8")
    assert result.returncode == 1
    assert ", ".join(fixture.meta["projects"][kind]) in result.stderr
    assert not list(tmp_path.glob("out.*"))


@pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
@pytest.mark.parametrize("kind", ["sch", "pcb"])
def test_a_project_is_named_by_its_name_alone_when_no_other_has_it(kind, tmp_path):
    fixture = SEVERAL
    board = fixture.meta["projects"][kind][1]
    result = subprocess.run([*shlex.split(KICAD_CLI), kind, "import", "--project",
                             sch_import.board_name(board), "-o", str(tmp_path / f"out.kicad_{kind}"),
                             str(fixture.source)],
                            env=kicad_env(tmp_path), capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert f"; importing {board} (left out: " in result.stdout
