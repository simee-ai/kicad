"""Prove `kicad-cli pcb import` converts real boards (simee/<version> builds): import each fixture's board,
compare the nets its gerbers give its pads with the source tool's, and draw it as simee-db does
(smoke.check_drawings). A fixture's board is the "board" of its fixture.json, next to its schematic."""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from kicad_bundle import sch_import, smoke


@dataclass(frozen=True)
class Board:
    name: str
    source: Path
    format: str
    size: tuple[float, float]  # its outline, mm
    nets: list[list[str]]  # "REF.PAD" lists, sorted


def boards() -> list[Board]:
    return [Board(f.name, f.source.parent / b["source"], f.format, tuple(b["size_mm"]), b["nets"])
            for f in sch_import.fixtures() if (b := f.meta.get("board"))]


def imported(cli: list[str], board: Board, tmp: Path) -> list[list[str]]:
    """Import board into tmp/out/imported.kicad_pcb; returns the nets its gerbers give its pads."""
    src, out = tmp / "src" / board.source.name, tmp / "out" / "imported.kicad_pcb"
    src.parent.mkdir()
    out.parent.mkdir()
    shutil.copy2(board.source, src)
    smoke.run(cli, ["pcb", "import", "--format", board.format, "-o", str(out), str(src)], out)
    return smoke.copper_nets(smoke.export_gerbers(cli, out, tmp / "gerbers"))


def check(cli: list[str], board: Board) -> None:
    """Raise unless KiCad's import of board has exactly the expected nets and draws."""
    with tempfile.TemporaryDirectory() as tmp:
        nets = imported(cli, board, Path(tmp))
        missing = [n for n in board.nets if n not in nets]
        extra = [n for n in nets if n not in board.nets]
        if missing or extra:
            raise RuntimeError(f"{board.name}: nets differ: missing {missing}, unexpected {extra}")
        smoke.check_drawings(cli, Path(tmp) / "out" / "imported.kicad_pcb", board.size, Path(tmp))
