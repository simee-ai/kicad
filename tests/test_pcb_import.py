import os
import re
import shlex
import xml.etree.ElementTree as ET

import pytest

from eagle_nets import eagle_board
from kicad_bundle import pcb_import

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


def test_boards_cover_eagle():
    assert "eagle" in {b.format for b in BOARDS}


def test_an_eagle_board_has_items_on_a_layer_kicad_doesnt_map():
    # The case #20 fixed: fiducials and logos drawn on tRestrict, as Adafruit's are
    layers = {w.get("layer") for b in BOARDS if b.format == "eagle" for w in ET.parse(b.source).getroot().iter("wire")}
    assert layers & UNMAPPED


@pytest.mark.parametrize("board", [b for b in BOARDS if b.format == "eagle"],
                         ids=[b.name for b in BOARDS if b.format == "eagle"])
def test_eagle_expectations_are_what_eagle_connects(board):
    assert eagle_board(board.source) == (board.size, board.nets)


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
