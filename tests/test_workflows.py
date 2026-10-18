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


def _block(lines, key, indent):
    """The lines under `key:` at this indent (its value on the same line first, if any), up to the next key at that
    indent or less; None if the key isn't there."""
    for i, line in enumerate(lines):
        if re.match(rf"^ {{{indent}}}{key}:", line):
            out = [line.split(":", 1)[1].strip()]
            for following in lines[i + 1:]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                out.append(following.strip())
            out = [re.sub(r"\s+#.*", "", part) for part in out]
            return [part for part in out if part and not part.startswith("#")]
    return None


def _jobs(text):
    """Each job of a workflow: its name and its lines."""
    jobs, name = {}, None
    in_jobs = False
    for line in text.splitlines():
        if re.match(r"^\S", line):
            in_jobs = line.startswith("jobs:")
            continue
        if not in_jobs:
            continue
        if match := re.match(r"^  ([\w-]+):", line):
            name = match.group(1)
            jobs[name] = []
        elif name:
            jobs[name].append(line)
    return jobs


def _writes(permissions):
    return permissions is not None and any(p.endswith("write") or p.endswith("write-all") for p in permissions)


def test_only_a_publishing_job_gets_a_write_token():
    # kicad-sync runs package.yml (publish=false) on its repair agent's fix branch before a human reviews it, so
    # that branch's code must not run with a token that can push, move tags or make releases (simee-kicad#41).
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text()
        top = _block(text.splitlines(), "permissions", 0)
        assert top is not None and not _writes(top), f"{workflow.name}: top-level permissions {top}"
        assert "github.token" not in "\n".join(_block(text.splitlines(), "env", 0) or []), \
            f"{workflow.name}: the token is in the workflow's env, so every job's steps get it"
        for job, lines in _jobs(text).items():
            if _writes(_block(lines, "permissions", 4)):
                condition = " ".join(_block(lines, "if", 4) or [])
                assert "inputs.publish" in condition, f"{workflow.name}: job {job} writes but doesn't only publish"


def test_each_release_job_can_publish():
    for workflow in ("package.yml", "arm-gcc.yml", "avr-gcc.yml"):
        lines = _jobs((WORKFLOWS / workflow).read_text())["release"]
        assert "contents: write" in (_block(lines, "permissions", 4) or []), workflow
