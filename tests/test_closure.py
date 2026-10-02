from pathlib import Path

from kicad_bundle.closure import closure


def test_follows_dependencies_and_skips_system_libraries():
    graph = {"cli": ["libA", "libB", "System"], "libA": ["libC"], "libB": ["libC"], "libC": [], "kiface": ["libD"],
             "libD": []}
    resolve = lambda name, _from: None if name == "System" else Path(name)
    found = closure([Path("cli"), Path("kiface")], deps=lambda p: graph[p.name], resolve=resolve,
                    exists=lambda p: True)
    assert found == {Path(n) for n in ("cli", "libA", "libB", "libC", "kiface", "libD")}


def test_reports_unresolvable_dependencies():
    missing = []
    closure([Path("cli")], deps=lambda p: ["gone"] if p.name == "cli" else [],
            resolve=lambda name, _from: Path(name), exists=lambda p: p.name != "gone", missing=missing)
    assert missing == [("cli", "gone")]
