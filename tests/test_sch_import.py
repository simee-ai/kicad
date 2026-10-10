import json
import os
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from eagle_nets import eagle_nets, kicad_reference
from kicad_bundle import sch_import
from kicad_bundle.smoke import netlist_components

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
