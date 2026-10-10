import subprocess
from pathlib import Path

import pytest

from kicad_bundle import tag_target
from kicad_bundle.tag_target import BranchMoved, target

INPUTS = ["avr_toolchain", "pyproject.toml", ".github/workflows/avr-gcc.yml"]


def test_the_build_commit_when_no_workflow_differs_from_the_default_branch():
    assert target("aaa", "bbb", ["avr_toolchain/build.py", "README.md"], INPUTS) == "aaa"
    assert target("aaa", "aaa", [], INPUTS) == "aaa"


def test_the_default_branch_head_when_a_workflow_differs_but_the_build_inputs_do_not():
    changed = [".github/workflows/package.yml", "kicad_bundle/linux.py", "avr_toolchainx/a.py"]
    assert target("aaa", "bbb", changed, INPUTS) == "bbb"


def test_refuses_clearly_when_a_workflow_and_a_build_input_differ():
    with pytest.raises(BranchMoved) as raised:
        target("aaa", "bbb", [".github/workflows/package.yml", "avr_toolchain/build.py"], INPUTS)
    message = str(raised.value)
    assert "avr_toolchain/build.py" in message
    assert "dispatch" in message.lower()
    assert "403" in message


def test_the_workflow_itself_is_a_build_input():
    with pytest.raises(BranchMoved):
        target("aaa", "bbb", [".github/workflows/avr-gcc.yml"], INPUTS)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo, files):
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """origin with branch main, and a shallow clone of its first commit (as actions/checkout leaves it)."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    built = _commit(origin, {"avr_toolchain/build.py": "1", ".github/workflows/package.yml": "1"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth=1", f"file://{origin}", str(clone)], check=True)
    return origin, clone, built


def test_main_prints_the_head_when_only_another_workflow_moved_on(repos, capsys):
    origin, clone, built = repos
    head = _commit(origin, {".github/workflows/package.yml": "2"})
    assert tag_target.main(["--sha", built, "--repo", str(clone), *INPUTS]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == head
    assert "main" in err and head in err  # says why it tags another commit


def test_main_prints_the_build_commit_when_the_branch_did_not_move(repos, capsys):
    _, clone, built = repos
    assert tag_target.main(["--sha", built, "--repo", str(clone), *INPUTS]) == 0
    assert capsys.readouterr().out.strip() == built


def test_main_fails_when_the_build_inputs_moved_on_too(repos, capsys):
    origin, clone, built = repos
    _commit(origin, {".github/workflows/package.yml": "2", "avr_toolchain/build.py": "2"})
    assert tag_target.main(["--sha", built, "--repo", str(clone), *INPUTS]) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert "avr_toolchain/build.py" in err and "main" in err


@pytest.mark.parametrize("workflow", ["avr-gcc.yml", "package.yml"])
def test_each_release_tags_what_tag_target_picks(workflow):
    text = (Path(__file__).parent.parent / ".github/workflows" / workflow).read_text()
    assert '--target "$GITHUB_SHA"' not in text
    assert '--target "$target"' in text
    call = " ".join(text.split("kicad_bundle.tag_target", 1)[1].split(")", 1)[0].split())
    assert f".github/workflows/{workflow}" in call  # the workflow's own steps are a build input
