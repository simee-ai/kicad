"""The import checks a bundle built from a simee/<version> branch passes on top of the smoke test:
`sch import` of every fixture's schematic, and its choice of importer for each detection case (sch_import), and
`pcb import` of every fixture's board (pcb_import)."""

from kicad_bundle import pcb_import, sch_import


def check(cli: list[str]) -> None:
    for fixture in sch_import.fixtures():
        sch_import.check(cli, fixture)
    sch_import.check_detection(cli)
    for board in pcb_import.boards():
        pcb_import.check(cli, board)
