import json
import os
import re
import zipfile
import shlex
import xml.etree.ElementTree as ET

import pytest

from eagle_nets import eagle_board
from easyedapro_nets import easyedapro_board
from kicad_bundle import pcb_import, sch_import

BOARDS = pcb_import.boards()
IDS = [b.name for b in BOARDS]
# A kicad-cli built from a simee/<version> branch; the import tests skip without it.
KICAD_CLI = os.environ.get("KICAD_CLI")
# Eagle layers KiCad's importer maps to no KiCad layer, whose package wires were saved on an undefined
# layer that made the imported board unloadable (#20): tRestrict, bRestrict, vRestrict, Milling.
UNMAPPED = {"41", "42", "43", "46"}

TINY_BOARD = """<eagle><drawing><board>
  <libraries><library name="l"><packages><package name="P">
    <wire x1="-50" y1="0" x2="50" y2="0" width="0" layer="20"/>
  </package></packages></library></libraries>
  <plain>
    <wire x1="2" y1="0" x2="0" y2="2" width="0" layer="20" curve="-90"/>
    <wire x1="0" y1="2" x2="0" y2="10" width="0" layer="20"/>
    <wire x1="0" y1="10" x2="20.5" y2="10" width="0" layer="20"/>
    <wire x1="20.5" y1="10" x2="2" y2="0" width="0" layer="20"/>
    <wire x1="-5" y1="-5" x2="30" y2="30" width="0.2" layer="21"/>
  </plain>
  <signals>
    <signal name="GND"><contactref element="U$1" pad="P$2"/><contactref element="C1" pad="1"/></signal>
    <signal name="N$1"><contactref element="C1" pad="2"/></signal>
    <signal name="N$2"><wire x1="0" y1="0" x2="1" y2="1" width="0.2" layer="1"/></signal>
  </signals>
</board></drawing></eagle>"""


def test_eagle_board_reads_the_outline_and_the_signals(tmp_path):
    brd = tmp_path / "tiny.brd"
    brd.write_text(TINY_BOARD)
    # silkscreen and a package's outline don't count; a signal with no pads isn't a net
    assert eagle_board(brd) == ((20.5, 10.0), [["C1.1", "U$1.P$2"], ["C1.2"]])


def test_boards_cover_eagle_and_altium():
    assert {"eagle", "altium"} <= {b.format for b in BOARDS}


def test_an_eagle_board_has_items_on_a_layer_kicad_doesnt_map():
    # The case #20 fixed: fiducials and logos drawn on tRestrict, as Adafruit's are
    layers = {w.get("layer") for b in BOARDS if b.format == "eagle" for w in ET.parse(b.source).getroot().iter("wire")}
    assert layers & UNMAPPED


@pytest.mark.parametrize("board", [b for b in BOARDS if b.format == "eagle"],
                         ids=[b.name for b in BOARDS if b.format == "eagle"])
def test_eagle_expectations_are_what_eagle_connects(board):
    assert eagle_board(board.source) == (board.size, board.nets)


def test_a_format_pcb_import_has_no_hint_for_is_found_by_its_content():
    # `pcb import --format` names no EasyEDA importer, though auto finds it (#29)
    assert pcb_import.format_hint("eagle") == "eagle"
    assert pcb_import.format_hint("easyedapro") == "auto"


def test_boards_cover_easyedapro():
    assert "easyedapro" in {b.format for b in BOARDS}


@pytest.mark.parametrize("board", [b for b in BOARDS if b.format == "easyedapro"],
                         ids=[b.name for b in BOARDS if b.format == "easyedapro"])
def test_easyedapro_expectations_are_what_easyeda_pro_connects(board):
    _, size, nets = easyedapro_board(board.source)
    assert (board.size, board.nets) == (size, nets)


def test_check_fails_when_the_nets_differ(monkeypatch):
    board = BOARDS[0]
    monkeypatch.setattr(pcb_import, "imported", lambda cli, b, tmp: board.nets[1:])
    monkeypatch.setattr(pcb_import.smoke, "check_drawings", lambda *a: None)
    with pytest.raises(RuntimeError, match=re.escape(f"{board.name}: nets differ: missing [{board.nets[0]!r}]")):
        pcb_import.check(["kicad-cli"], board)


@pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
@pytest.mark.parametrize("board", BOARDS, ids=IDS)
def test_import_matches_source_tool(board):
    pcb_import.check(shlex.split(KICAD_CLI), board)


def test_the_import_of_a_board_naming_a_project_passes_it(monkeypatch, tmp_path):
    board = next(b for b in BOARDS if b.project)
    calls = []

    def run(cli, args, out):
        calls.append(args)
        raise StopIteration

    monkeypatch.setattr(pcb_import.smoke, "run", run)
    with pytest.raises(StopIteration):
        pcb_import.imported(["kicad-cli"], board, tmp_path)
    assert calls[0][:2] == ["pcb", "import"] and calls[0][-3:-1] == ["--project", board.project]


def test_check_fails_when_a_text_of_the_board_is_missing(monkeypatch):
    board = next(b for b in BOARDS if b.texts)

    def imported(cli, b, tmp):
        (tmp / "out").mkdir()
        (tmp / "out" / "imported.kicad_pcb").write_text(f'(kicad_pcb (gr_text "{board.texts[0]}"))')
        return board.nets

    monkeypatch.setattr(pcb_import, "imported", imported)
    monkeypatch.setattr(pcb_import.smoke, "check_drawings", lambda *a: None)
    with pytest.raises(RuntimeError, match=re.escape(f"{board.name}: texts missing: {list(board.texts[1:])!r}")):
        pcb_import.check(["kicad-cli"], board)


@pytest.mark.parametrize("board", [b for b in BOARDS if b.texts], ids=[b.name for b in BOARDS if b.texts])
def test_a_boards_texts_are_its_own_and_not_the_first_boards(board):
    # what tells a board named by --project from the first, when both have the same nets (#45)
    with zipfile.ZipFile(board.source) as zf:
        project = json.loads(zf.read("project.json"))
        def texts(name):
            pcb = project["boards"][name]["pcb"]
            return {json.loads(line)[6] for line in zf.read(f"PCB/{pcb}.epcb").decode().splitlines()
                    if line.startswith('["STRING"')}
        own, first = texts(sch_import.board_name(board.project)), texts(min(project["boards"]))
    assert set(board.texts) <= own - first
