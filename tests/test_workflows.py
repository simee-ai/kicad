import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def _steps(text):
    """Each step of a workflow as its lines: a step starts at a `- ` list item under `steps:`."""
    steps, step = [], None
    for line in text.splitlines():
        if re.match(r"\s*- (name|uses|run|id|if):", line):
            step = [line]
            steps.append(step)
        elif step is not None:
            step.append(line)
    return steps


def _cache_saves():
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for step in _steps(workflow.read_text()):
            if any("actions/cache/save@" in line for line in step):
                yield workflow.name, step


def test_the_windows_job_saves_the_vcpkg_ports():
    assert any("vcpkg-binaries" in "\n".join(step) for _, step in _cache_saves())


def test_a_cancelled_run_saves_no_cache():
    # A cancelled run's partial cache would be the newest entry, so later runs would restore it instead
    # of a complete one and rebuild most ports (simee-kicad#39). A failed or timed-out run still saves.
    for name, step in _cache_saves():
        condition = next((line for line in step if line.strip().startswith("if:")), "")
        assert "!cancelled()" in condition, f"{name}: {step[0].strip()}"
        assert "always()" not in condition, f"{name}: {step[0].strip()}"
