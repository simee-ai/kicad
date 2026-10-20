"""Prove `kicad-cli pcb import` converts real boards (simee/<version> builds): import each fixture's board,
compare the nets its gerbers give its pads with the source tool's, and draw it as simee-db does
(smoke.check_drawings). A fixture's board is the "board" of its fixture.json, next to its schematic; its nets are
the schematic's unless it names its own. It imports the fixture's project, if it names one (sch_import), and must
have the board's "texts" (silkscreen and the like) as texts of its own."""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kicad_bundle import sch_import, smoke


# The formats `pcb import --format` takes a hint for. Any other (EasyEDA Pro) is found by the file's content.
FORMAT_HINTS = {"pads", "altium", "eagle", "cadstar", "fabmaster", "pcad", "solidworks"}


def format_hint(format: str) -> str:
    return sch_import.format_hint(format, FORMAT_HINTS)


@dataclass(frozen=True)
class Board:
    name: str
    source: Path
    format: str
    size: tuple[float, float]  # its outline, mm
    nets: list[list[str]]  # "REF.PAD" lists, sorted
    project: str | None = None  # of a source holding several, as kicad-cli names it; None for the first
    texts: tuple[str, ...] = ()  # the board's own texts (gr_text), such as what tells it from another of its file


def boards() -> list[Board]:
    return [Board(f.name, f.source.parent / b["source"], f.format, tuple(b["size_mm"]), b.get("nets", f.nets),
                  f.project, tuple(b.get("texts", ())))
            for f in sch_import.fixtures() if (b := f.meta.get("board"))]


def imported(cli: list[str], board: Board, tmp: Path) -> list[list[str]]:
    """Import board into tmp/out/imported.kicad_pcb; returns the nets its gerbers give its pads."""
    src, out = tmp / "src" / board.source.name, tmp / "out" / "imported.kicad_pcb"
    src.parent.mkdir()
    out.parent.mkdir()
    shutil.copy2(board.source, src)
    smoke.run(cli, ["pcb", "import", "--format", format_hint(board.format), "-o", str(out),
                    *sch_import.project_args(board.project), str(src)], out)
    return smoke.copper_nets(smoke.export_gerbers(cli, out, tmp / "gerbers"))


def check(cli: list[str], board: Board) -> None:
    """Raise unless KiCad's import of board has exactly the expected nets, its texts, and draws."""
    with tempfile.TemporaryDirectory() as tmp:
        nets = imported(cli, board, Path(tmp))
        missing = [n for n in board.nets if n not in nets]
        extra = [n for n in nets if n not in board.nets]
        if missing or extra:
            raise RuntimeError(f"{board.name}: nets differ: missing {missing}, unexpected {extra}")
        pcb = Path(tmp) / "out" / "imported.kicad_pcb"
        text = pcb.read_text(encoding="utf-8")
        if missing_texts := [t for t in board.texts if f'(gr_text "{t}"' not in text]:
            raise RuntimeError(f"{board.name}: texts missing: {missing_texts}")
        smoke.check_drawings(cli, pcb, board.size, Path(tmp))
