import json
import os
import shlex

import pytest

from eagle_nets import eagle_nets
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


@pytest.mark.parametrize("fixture", FIXTURES, ids=IDS)
def test_fixture_is_redistributable(fixture):
    assert fixture.source.is_file()
    for key in ("origin", "licence", "licence_file", "checked"):
        assert fixture.meta.get(key), f"{fixture.name}: fixture.json needs {key}"
    assert (fixture.source.parent / fixture.meta["licence_file"]).is_file()


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
