"""What KiCad's macOS builder (kicad-mac-builder) builds itself rather than taking from Homebrew:
KiCad's wxWidgets fork, ngspice, and the relocatable python.org Python. Their versions are pinned on
the builder's release branch (10.0 for KiCad 10.0.x), read as it was when KiCad published the release;
a pin naming a branch rather than a tag or commit resolves to that branch's head at the same moment."""

import json
import re
import subprocess
import tarfile
import tempfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from kicad_bundle.fetch import Fetch

GITLAB = "https://gitlab.com/api/v4/projects"
BUILDER = "kicad/packaging/kicad-mac-builder"
SHA = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class GitSource:
    url: str
    ref: str  # tag, branch or commit


@dataclass(frozen=True)
class Pins:
    builder: str  # the kicad-mac-builder commit read
    wxwidgets: GitSource
    ngspice: GitSource
    python: str  # python.org version
    wx_cmake: str = ""  # its wx.cmake, which says how it configures and builds wxWidgets


@dataclass(frozen=True)
class WxBuild:
    env: dict[str, str]
    configure: list[str]  # ./configure's arguments
    make: list[str]  # make's arguments (KiCad release builds)


def _project(path: str) -> str:
    return f"{GITLAB}/{urllib.parse.quote(path, safe='')}/repository"


def _head_at(project: str, ref: str, until: str, fetch: Fetch) -> str:
    query = urllib.parse.urlencode({"ref_name": ref, "until": until, "per_page": 1})
    return json.loads(fetch(f"{_project(project)}/commits?{query}"))[0]["id"]


def external_projects(cmake: str) -> dict[str, GitSource]:
    found = {}
    for name, body in re.findall(r"ExternalProject_Add\(\s*(\w+)(.*?)\n\s*\)", cmake, re.S):
        url, ref = re.search(r"GIT_REPOSITORY\s+(\S+)", body), re.search(r"GIT_TAG\s+(\S+)", body)
        if url and ref:
            found[name] = GitSource(url.group(1), ref.group(1))
    return found


def pins(version: str, until: str, fetch: Fetch) -> Pins:
    branch = ".".join(version.split(".")[:2])
    commit = _head_at(BUILDER, branch, until, fetch)

    def read(path: str) -> str:
        return fetch(f"{_project(BUILDER)}/files/{urllib.parse.quote(path, safe='')}/raw?ref={commit}").decode()

    wx_cmake = read("kicad-mac-builder/wx.cmake")
    wx = external_projects(wx_cmake)["wxwidgets"]
    ngspice = external_projects(read("kicad-mac-builder/ngspice.cmake"))["ngspice"]
    python = re.search(r"set\(\s*PYTHON_VERSION\s+(\S+)\s*\)", read("kicad-mac-builder/CMakeLists.txt")).group(1)
    https = GitSource(re.sub(r"^git://", "https://", ngspice.url), ngspice.ref)  # git:// is often firewalled
    return Pins(commit, wx, https, python, wx_cmake)


def wx_build(cmake: str, minos: str, prefix: str) -> WxBuild:
    """How kicad-mac-builder's wx.cmake configures and makes wxWidgets for a release build."""
    def expand(text: str) -> str:
        return text.replace("${MACOS_MIN_VERSION}", minos).replace("${wxwidgets_INSTALL_DIR}", prefix)

    body = re.search(r"ExternalProject_Add\(\s*wxwidgets\b(.*?)\n\s*\)", cmake, re.S).group(1)
    command = re.search(r"CONFIGURE_COMMAND\s+(.*?)\n\s*[A-Z_]+_COMMAND\b", body, re.S).group(1).split()
    at = command.index("./configure")
    env = dict(expand(word).split("=", 1) for word in command[:at])
    make_args = re.search(r'STREQUAL\s+"Release"\s*\)\s*set\(\s*wxwidgets_MAKE_ARGS\s+"([^"]*)"', cmake).group(1)
    return WxBuild(env, [expand(word) for word in command[at + 1:]], make_args.split())


def _ls_remote(url: str, ref: str) -> str:
    return subprocess.run(["git", "ls-remote", url, ref], capture_output=True, text=True, check=True).stdout


def commit(source: GitSource, until: str, fetch: Fetch, ls_remote: Callable[[str, str], str] = _ls_remote) -> str:
    """The commit a pin built: itself, the commit a tag names, or a branch's head at until (GitLab only)."""
    if SHA.fullmatch(source.ref):
        return source.ref
    refs = {name: sha for sha, name in (line.split("\t") for line in ls_remote(source.url, source.ref).splitlines())}
    tag = f"refs/tags/{source.ref}"
    if found := refs.get(f"{tag}^{{}}") or refs.get(tag):
        return found
    if f"refs/heads/{source.ref}" in refs and source.url.startswith("https://gitlab.com/"):
        return _head_at(source.url.removeprefix("https://gitlab.com/").removesuffix(".git"), source.ref, until, fetch)
    raise RuntimeError(f"{source}: not a tag or commit, and a branch can only be dated on GitLab")


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "protocol.file.allow=always", "-c", "advice.detachedHead=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def git_archive(source: GitSource, sha: str, name: str, cache: Path) -> Path:
    """cache/git/<name>-<sha[:12]>.tar.gz: the tree at sha with every submodule, no git metadata."""
    top = f"{name}-{sha[:12]}"
    dest = cache / "git" / f"{top}.tar.gz"
    if dest.exists():
        dest.touch()
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        work = Path(tmp) / "checkout"
        work.mkdir()
        _git("init", "-q", cwd=work)
        _git("fetch", "-q", "--depth", "1", source.url, sha, cwd=work)
        _git("checkout", "-q", "FETCH_HEAD", cwd=work)
        _git("submodule", "update", "-q", "--init", "--recursive", "--depth", "1", cwd=work)
        part = Path(tmp) / dest.name
        with tarfile.open(part, "w:gz") as tar:
            for path in sorted(work.rglob("*")):
                rel = path.relative_to(work)
                if ".git" not in rel.parts and (path.is_file() or path.is_symlink()):
                    tar.add(path, arcname=f"{top}/{rel}", recursive=False)
        part.replace(dest)
    return dest
