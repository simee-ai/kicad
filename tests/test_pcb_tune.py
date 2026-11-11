"""`kicad-cli pcb tune` (#52): KiCad's length tuner as a command, each case judged by `pcb drc` with length and
skew rules. The tests that run kicad-cli skip without KICAD_CLI (a kicad-cli built from a simee/<version> branch)."""

import collections
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from kicad_bundle import cache, fetch, pcb_tune
from kicad_bundle.pcb_tune import length_rule, project, rule, set_rules, tune, violations

KICAD_CLI = os.environ.get("KICAD_CLI")
needs_cli = pytest.mark.skipif(not KICAD_CLI, reason="set KICAD_CLI to a kicad-cli built from a simee/<version> branch")
SKEW_AB = rule("A-B skew", "A.NetName == 'SIG_A' || A.NetName == 'SIG_B'", "skew (max 0.2mm)")
PAIR_SKEW = rule("USB skew", "A.inDiffPair('USB')", "skew (max 0.05mm)")
PAIR_LENGTH = rule("USB 46", "A.inDiffPair('USB')", "length (min 45.9mm) (max 46.1mm)")
# gusmanb/logicanalyzer's original board (GPL-3.0): fetched at this commit, never committed. Its 24 channels
# /CHAN_0../CHAN_23 are hand-matched to about 50 mm, and U1 (a Raspberry Pi Pico) has two overlapping pads per
# pin, a castellated SMD pad on F.Cu over a through-hole one.
REFERENCE_URL = ("https://raw.githubusercontent.com/gusmanb/logicanalyzer/fc05977ad4cca3fd813de80bf25d0a207402dffb/"
                 "Electronics/LogicAnalyzer/LogicAnalyzer/LogicAnalyzer.kicad_pcb")
REFERENCE_SHA256 = "214aa00d0b66dee2ec75c81a8ea1f396ef435975a382fe42989a3203b59c80ea"
CHANNELS = [f"/CHAN_{i}" for i in range(24)]


@pytest.fixture
def cli() -> list[str]:
    return shlex.split(KICAD_CLI)


def test_the_fixture_board_is_the_same_every_time():
    assert pcb_tune.board_text() == pcb_tune.board_text()
    assert pcb_tune.board_text().count("(segment ") == len(pcb_tune.TRACKS)


def test_a_length_rule_names_its_opt_only_when_given():
    assert length_rule("SIG_A", 44.9, 45.1) == (
        "(rule \"SIG_A length\" (condition \"A.NetName == 'SIG_A'\") (constraint length (min 44.9mm) (max 45.1mm)))")
    assert "(opt 45mm)" in length_rule("SIG_A", 44.9, 45.1, 45)


@needs_cli
def test_one_net_to_a_length_beside_an_obstacle(cli, tmp_path):
    pcb = project(tmp_path, "one", [length_rule("SIG_A", 44.9, 45.1)])
    assert violations(cli, pcb) == ["length_out_of_range"]
    code, nets = tune(cli, pcb, "--net", "SIG_A", "--length", "45")
    assert code == 0
    assert violations(cli, pcb) == []
    assert pcb_tune.patterns(pcb) == 1
    net = nets["SIG_A"]
    assert (net["mode"], net["action"], net["status"], net["tuner_status"]) == ("single", "placed", "tuned", "tuned")
    assert net["length_before"] == pytest.approx(40, abs=1e-3)
    assert net["length_after"] == pytest.approx(45, abs=0.1)
    assert net["target"] == pytest.approx({"min": 44.9, "opt": 45, "max": 45.1})


@needs_cli
def test_a_tuned_net_retunes_up_and_down_with_its_one_pattern(cli, tmp_path):
    pcb = project(tmp_path, "one", [])
    tune(cli, pcb, "--net", "SIG_A", "--length", "45")
    for length in (47, 43):
        set_rules(pcb, [length_rule("SIG_A", length - 0.1, length + 0.1)])
        code, nets = tune(cli, pcb, "--net", "SIG_A", "--length", str(length))
        assert (code, nets["SIG_A"]["action"]) == (0, "retuned")
        assert violations(cli, pcb) == []
        assert pcb_tune.patterns(pcb) == 1


@needs_cli
def test_a_net_within_its_target_is_left_alone(cli, tmp_path):
    pcb = project(tmp_path, "one", [])
    code, nets = tune(cli, pcb, "--net", "SIG_A", "--length", "40.05")
    assert (code, nets["SIG_A"]["action"], nets["SIG_A"]["status"]) == (0, "skipped", "tuned")
    assert pcb_tune.patterns(pcb) == 0


@needs_cli
def test_a_net_longer_than_its_target_is_too_long(cli, tmp_path):
    # Meanders only lengthen
    pcb = project(tmp_path, "one", [])
    code, nets = tune(cli, pcb, "--net", "SIG_A", "--length", "30")
    assert (code, nets["SIG_A"]["status"]) == (5, "too_long")
    assert pcb_tune.patterns(pcb) == 0


@needs_cli
@pytest.mark.parametrize("opt", [None, 45])
def test_one_net_to_the_boards_length_rule(cli, tmp_path, opt):
    # Without an opt, the middle of the rule's range: KiCad's tuner would stop at its min and fail DRC
    pcb = project(tmp_path, "rule", [length_rule("SIG_A", 44.9, 45.1, opt)])
    code, nets = tune(cli, pcb, "--net", "SIG_A")
    assert code == 0
    assert nets["SIG_A"]["target"]["opt"] == pytest.approx(45)
    assert violations(cli, pcb) == []


@needs_cli
def test_a_net_with_no_target_fails(cli, tmp_path):
    pcb = project(tmp_path, "none", [])
    code, nets = tune(cli, pcb, "--net", "SIG_A")
    assert (code, nets["SIG_A"]["status"]) == (5, "failed")
    assert "no target" in nets["SIG_A"]["error"]


@needs_cli
def test_a_skew_group_matches_the_shorter_net_to_the_longer(cli, tmp_path):
    pcb = project(tmp_path, "skew", [SKEW_AB])
    assert violations(cli, pcb) == ["skew_out_of_range"]
    code, nets = tune(cli, pcb, "--net", "SIG_A", "--net", "SIG_B")
    assert code == 0
    assert (nets["SIG_A"]["action"], nets["SIG_B"]["action"]) == ("placed", "skipped")
    assert violations(cli, pcb) == []


@needs_cli
def test_a_length_out_of_reach_fails_and_leaves_the_board_clean(cli, tmp_path):
    # 40 mm of track can't meander to 200 mm
    pcb = project(tmp_path, "tight", [])
    code, nets = tune(cli, pcb, "--net", "SIG_A", "--length", "200")
    assert (code, nets["SIG_A"]["status"]) == (5, "too_short")
    assert violations(cli, pcb) == []


@needs_cli
def test_an_unknown_net_is_an_argument_error(cli, tmp_path):
    pcb = project(tmp_path, "one", [])
    result = subprocess.run([*cli, "pcb", "tune", "--net", "NOPE", str(pcb)], capture_output=True,
                            env=pcb_tune.kicad_env(tmp_path))
    assert result.returncode == 1


@needs_cli
def test_a_pairs_skew_meanders_the_shorter_net(cli, tmp_path):
    pcb = project(tmp_path, "pskew", [PAIR_SKEW])
    assert violations(cli, pcb) == ["skew_out_of_range"]
    code, nets = tune(cli, pcb, "--mode", "skew", "--net", "USB_P")
    assert code == 0
    net = nets["USB_P"]
    assert (net["coupled_net"], net["status"]) == ("USB_N", "tuned")
    assert net["skew_before"] == pytest.approx(-0.4142, abs=1e-3)
    assert abs(net["skew_after"]) <= 0.05
    assert violations(cli, pcb) == []


@needs_cli
def test_a_pair_tuned_as_one_keeps_its_skew(cli, tmp_path):
    # KiCad tunes a pair to its longer net: the shorter stays short of the rule until its skew is tuned
    pcb = project(tmp_path, "plen", [PAIR_LENGTH])
    code, nets = tune(cli, pcb, "--mode", "diff-pair", "--net", "USB_P", "--length", "46")
    assert (code, nets["USB_P"]["status"]) == (5, "too_short")
    assert violations(cli, pcb) == ["length_out_of_range"]


@needs_cli
def test_a_pairs_skew_then_its_length(cli, tmp_path):
    pcb = project(tmp_path, "plen", [PAIR_LENGTH, PAIR_SKEW])
    assert violations(cli, pcb) == ["length_out_of_range", "length_out_of_range", "skew_out_of_range"]
    assert tune(cli, pcb, "--mode", "skew", "--net", "USB_P")[0] == 0
    code, nets = tune(cli, pcb, "--mode", "diff-pair", "--net", "USB_P", "--length", "46")
    assert (code, nets["USB_P"]["status"]) == (0, "tuned")
    assert violations(cli, pcb) == []
    assert pcb_tune.patterns(pcb) == 2


@needs_cli
def test_the_reference_boards_channels_to_52_mm(cli, tmp_path):
    # As DRC measures them: on U1's overlapping pads the tuner's own measure is off DRC's by up to 1.6 mm,
    # so tune retunes by the difference. Zones refilled by tune itself, so DRC finds only the board's own faults.
    source = fetch.cached_file("LogicAnalyzer.kicad_pcb", REFERENCE_URL, REFERENCE_SHA256,
                               cache.DEFAULT / cache.TEST_FIXTURES, fetch.fetch_url)
    original = project(tmp_path, "la", [], source.read_text(encoding="utf-8"))
    own = collections.Counter(violations(cli, original, refill=True))
    tuned = tmp_path / "t52.kicad_pcb"
    code, nets = tune(cli, original, *(a for n in CHANNELS for a in ("--net", n)), "--length", "52", output=tuned)
    assert code == 0, {n: (r["status"], r["length_after"]) for n, r in nets.items() if r["status"] != "tuned"}
    for net in CHANNELS:
        assert nets[net]["length_after"] == pytest.approx(52, abs=0.1), net
    tuned.with_suffix(".kicad_pro").write_text("{}\n", encoding="utf-8")
    set_rules(tuned, [rule("chan 52", "A.NetName == '/CHAN_*'", "length (min 51.9mm) (max 52.1mm)")])
    # Without refilling: tune refilled the pours around the meanders
    assert collections.Counter(violations(cli, tuned)) == own
