import json
import subprocess
import tarfile

import pytest

from kicad_bundle import macbuilder
from kicad_bundle.macbuilder import GitSource, Pins

WX_CMAKE = """include(ExternalProject)

ExternalProject_Add(
    wxwidgets
    PREFIX  wxwidgets
    GIT_REPOSITORY https://gitlab.com/kicad/code/wxWidgets.git
    GIT_TAG kicad/macos-wx-3.2
    CONFIGURE_COMMAND ./configure --with-zlib=builtin
 )

ExternalProject_Add(
    wxpython
    GIT_REPOSITORY https://github.com/wxWidgets/Phoenix.git
    GIT_TAG 78938da1218483024b3a7acf55b5fb5513882916
)
"""
NGSPICE_CMAKE = """ExternalProject_Add(
    ngspice
    PREFIX  ngspice
    GIT_REPOSITORY git://git.code.sf.net/p/ngspice/ngspice
    GIT_TAG ngspice-45.2
    UPDATE_COMMAND      ""
)
"""
LISTS = "set( PYTHON_VERSION 3.9.13 )\nset( PYTHON_X_Y_VERSION 3.9 )\n"
API = "https://gitlab.com/api/v4/projects"


def test_external_projects_reads_each_projects_git_source():
    assert macbuilder.external_projects(WX_CMAKE) == {
        "wxwidgets": GitSource("https://gitlab.com/kicad/code/wxWidgets.git", "kicad/macos-wx-3.2"),
        "wxpython": GitSource("https://github.com/wxWidgets/Phoenix.git", "78938da1218483024b3a7acf55b5fb5513882916")}


def test_pins_come_from_the_builders_release_branch_as_it_was_at_the_release():
    files = {"kicad-mac-builder/wx.cmake": WX_CMAKE, "kicad-mac-builder/ngspice.cmake": NGSPICE_CMAKE,
             "kicad-mac-builder/CMakeLists.txt": LISTS}

    def fetch(url: str) -> bytes:
        kmb = f"{API}/kicad%2Fpackaging%2Fkicad-mac-builder/repository"
        if url == f"{kmb}/commits?ref_name=10.0&until=2026-08-29T15%3A43%3A28Z&per_page=1":
            return json.dumps([{"id": "kmb1"}]).encode()
        for path, text in files.items():
            if url == f"{kmb}/files/{path.replace('/', '%2F')}/raw?ref=kmb1":
                return text.encode()
        raise AssertionError(url)

    assert macbuilder.pins("10.0.6", "2026-08-29T15:43:28Z", fetch) == Pins(
        "kmb1", GitSource("https://gitlab.com/kicad/code/wxWidgets.git", "kicad/macos-wx-3.2"),
        GitSource("https://git.code.sf.net/p/ngspice/ngspice", "ngspice-45.2"), "3.9.13")


def test_commit_of_a_tag_is_exact_and_of_a_gitlab_branch_is_its_head_at_the_release():
    refs = {"https://git.code.sf.net/p/ngspice/ngspice": "aaa\trefs/tags/ngspice-45.2\nbbb\trefs/tags/ngspice-45.2^{}\n",
            "https://gitlab.com/kicad/code/wxWidgets.git": "ccc\trefs/heads/kicad/macos-wx-3.2\n"}

    def fetch(url: str) -> bytes:
        assert url == (f"{API}/kicad%2Fcode%2FwxWidgets/repository/commits"
                       "?ref_name=kicad%2Fmacos-wx-3.2&until=2026-08-29T15%3A43%3A28Z&per_page=1")
        return json.dumps([{"id": "wx-then"}]).encode()

    def ls_remote(url: str, ref: str) -> str:
        return refs[url]

    until = "2026-08-29T15:43:28Z"
    assert macbuilder.commit(GitSource("https://git.code.sf.net/p/ngspice/ngspice", "ngspice-45.2"), until,
                             fetch, ls_remote) == "bbb"  # the commit an annotated tag points at
    assert macbuilder.commit(GitSource("https://gitlab.com/kicad/code/wxWidgets.git", "kicad/macos-wx-3.2"),
                             until, fetch, ls_remote) == "wx-then"
    sha = "78938da1218483024b3a7acf55b5fb5513882916"
    assert macbuilder.commit(GitSource("https://x/y.git", sha), until, fetch, ls_remote) == sha
    with pytest.raises(RuntimeError, match="branch"):
        macbuilder.commit(GitSource("https://github.com/x/y.git", "main"), until, fetch,
                          lambda url, ref: "ddd\trefs/heads/main\n")


def _git(*args, cwd):
    subprocess.run(["git", "-c", "protocol.file.allow=always", "-c", "user.name=t", "-c", "user.email=t@t",
                    *args], cwd=cwd, check=True, capture_output=True)


def test_git_archive_holds_the_commit_and_its_submodules_without_git_metadata(tmp_path):
    sub, top = tmp_path / "png", tmp_path / "wx"
    for repo in (sub, top):
        repo.mkdir()
        _git("init", "-q", cwd=repo)
    (sub / "LICENSE").write_text("png licence")
    _git("add", ".", cwd=sub)
    _git("commit", "-qm", "png", cwd=sub)
    (top / "README").write_text("wx")
    _git("add", "README", cwd=top)
    _git("submodule", "add", "-q", sub.as_uri(), "src/png", cwd=top)
    _git("commit", "-qm", "wx", cwd=top)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=top, capture_output=True, text=True).stdout.strip()

    archive = macbuilder.git_archive(GitSource(top.as_uri(), "main"), head, "wxWidgets", tmp_path / "cache")
    assert archive.name == f"wxWidgets-{head[:12]}.tar.gz"
    with tarfile.open(archive) as tar:
        names = sorted(m.name for m in tar if m.isfile())
    assert names == [f"wxWidgets-{head[:12]}/{n}" for n in (".gitmodules", "README", "src/png/LICENSE")]
    assert macbuilder.git_archive(GitSource("unused", "main"), head, "wxWidgets", tmp_path / "cache") == archive
