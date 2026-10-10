"""Which commit a workflow's release tags.

    python -m kicad_bundle.tag_target --sha "$GITHUB_SHA" <build input>...

prints the commit to pass to `gh release create --target`. GitHub won't let a workflow's token create a tag
at a commit whose `.github/workflows/` differs from the default branch's head (HTTP 403, "Resource not
accessible by integration"; the token has no `workflows` permission). So a release built from a commit the
default branch has since moved past, with a workflow changed, tags the head instead, as long as none of the
build inputs (the paths the release is built from) changed; otherwise it fails, asking for a new dispatch.
"""

import argparse
import subprocess
import sys

WORKFLOWS = ".github/workflows/"


class BranchMoved(Exception):
    pass


def _under(path: str, inputs: list[str]) -> bool:
    return any(path == i or path.startswith(i.rstrip("/") + "/") for i in inputs)


def target(sha: str, head: str, changed: list[str], inputs: list[str], branch: str = "the default branch") -> str:
    """changed: the paths that differ between sha (what was built) and head (the default branch's)."""
    if not any(p.startswith(WORKFLOWS) for p in changed):
        return sha
    moved = [p for p in changed if _under(p, inputs)]
    if not moved:
        return head
    raise BranchMoved(
        f"{branch} moved on to {head} during the build, changing .github/workflows/, so GitHub won't let this "
        f"run's token tag {sha} (HTTP 403), and changing what the release is built from, so {head} can't be "
        f"tagged instead: {', '.join(moved)}. Dispatch the workflow again on {branch}.")


def _git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True).stdout.strip()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sha", required=True, help="the commit the release was built from")
    parser.add_argument("--repo", default=".", help="a checkout with remote origin")
    parser.add_argument("inputs", nargs="+", help="paths the release is built from (folders or files)")
    args = parser.parse_args(argv)

    symref = _git(args.repo, "ls-remote", "--symref", "origin", "HEAD").splitlines()[0]
    branch = symref.split()[1].removeprefix("refs/heads/")  # "ref: refs/heads/<branch>\tHEAD"
    _git(args.repo, "fetch", "-q", "--no-tags", "--depth=1", "origin", branch)
    head = _git(args.repo, "rev-parse", "FETCH_HEAD")
    changed = _git(args.repo, "diff", "--name-only", args.sha, head).splitlines()
    try:
        tag = target(args.sha, head, changed, args.inputs, branch)
    except BranchMoved as e:
        print(e, file=sys.stderr)
        return 1
    if tag != args.sha:
        print(f"{branch} moved on to {head} during the build, changing .github/workflows/ but none of "
              f"{', '.join(args.inputs)}: tagging {head}, which a workflow's token may tag, instead of {args.sha}",
              file=sys.stderr)
    print(tag)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
