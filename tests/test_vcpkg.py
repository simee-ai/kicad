import hashlib
import json
import subprocess

import pytest

from kicad_bundle import vcpkg
from kicad_bundle.portfile import Download
from kicad_bundle.vcpkg import GitRepo, Registry

MANIFEST = json.dumps({"name": "kicad", "dependencies": ["curl"], "overrides": [
    {"name": "protobuf", "version": "3.21.12#4", "$comment": "pinned"}, {"name": "python3", "version": "3.11.5"}]})
CONFIGURATION = json.dumps({
    "default-registry": {"kind": "git", "repository": "https://github.com/microsoft/vcpkg", "baseline": "b" * 40},
    "registries": [{"kind": "git", "repository": "https://gitlab.com/kicad/packaging/kicad-vcpkg-registry.git",
                    "baseline": "k" * 40, "packages": ["python3", "wxwidgets-33"]}]})


def test_config_names_each_ports_registry_and_the_manifests_overrides():
    config = vcpkg.config(MANIFEST, CONFIGURATION)
    assert config.registry("curl") == Registry("https://github.com/microsoft/vcpkg", "b" * 40)
    assert config.registry("python3") == Registry("https://gitlab.com/kicad/packaging/kicad-vcpkg-registry.git",
                                                  "k" * 40, ("python3", "wxwidgets-33"))
    assert config.overrides == {"protobuf": ("3.21.12", 4), "python3": ("3.11.5", 0)}


class FakeRepo:
    """A registry: versions/ at its baseline commit, and port trees by id."""

    def __init__(self, files: dict[str, str], trees: dict[str, dict[str, bytes]]):
        self.files, self.trees = files, trees

    def show(self, commit: str, path: str) -> bytes:
        assert commit == "b" * 40
        return self.files[path].encode()

    def tree(self, tree: str) -> dict[str, bytes]:
        return self.trees[tree]


def test_port_takes_the_baseline_version_unless_the_manifest_overrides_it():
    repo = FakeRepo({
        "versions/baseline.json": json.dumps({"default": {"zlib": {"baseline": "1.3.1", "port-version": 0},
                                                          "protobuf": {"baseline": "5.29.5", "port-version": 3}}}),
        "versions/z-/zlib.json": json.dumps({"versions": [{"version": "1.3.1", "git-tree": "z1"},
                                                          {"version": "1.3", "port-version": 1, "git-tree": "z0"}]}),
        "versions/p-/protobuf.json": json.dumps({"versions": [
            {"version": "5.29.5", "port-version": 3, "git-tree": "p5"},
            {"version-semver": "3.21.12", "port-version": 4, "git-tree": "p3"}]})},
        {"z1": {"portfile.cmake": b"zlib"}, "p3": {"portfile.cmake": b"protobuf"}})
    config = vcpkg.config(MANIFEST, CONFIGURATION)
    zlib = vcpkg.port(config, "zlib", lambda registry: repo)
    assert (zlib.name, zlib.version, zlib.port_version, zlib.tree, zlib.files) == (
        "zlib", "1.3.1", 0, "z1", {"portfile.cmake": b"zlib"})
    protobuf = vcpkg.port(config, "protobuf", lambda registry: repo)
    assert (protobuf.version, protobuf.port_version, protobuf.tree) == ("3.21.12", 4, "p3")


def test_port_fails_when_the_registry_lacks_the_version():
    repo = FakeRepo({"versions/baseline.json": json.dumps({"default": {"zlib": {"baseline": "9", "port-version": 0}}}),
                     "versions/z-/zlib.json": json.dumps({"versions": []})}, {})
    with pytest.raises(RuntimeError, match="zlib 9#0"):
        vcpkg.port(vcpkg.config(MANIFEST, CONFIGURATION), "zlib", lambda registry: repo)


def test_built_ports_and_source_dirs_come_from_the_build_paths_in_a_binary():
    data = (b"\0C:\\vcpkg\\buildtrees\\libgit2\\x64-windows-rel\\git2.pdb\0"
            b"C:\\vcpkg\\buildtrees\\libgit2\\src\\v1.9.2-5f7ecac20a.clean\\src\\util\\errors.c\0"
            b"C:\\jenkins\\workspace\\build\\kicad.pdb\0")
    assert vcpkg.built_ports(data) == {"libgit2"}
    assert vcpkg.source_dirs(data) == {("libgit2", "5f7ecac20a")}


def test_fingerprint_is_vcpkgs_hash_of_the_archive_and_its_patches():
    files = {"a.patch": b"A", "b.diff": b"B"}
    d = Download(("u",), "f.tar.gz", "ff" * 64, ("a.patch", "b.diff"), main=True)
    text = "ff" * 64 + hashlib.sha512(b"A").hexdigest() + hashlib.sha512(b"B").hexdigest()
    assert vcpkg.fingerprint(d, files) == hashlib.sha512(text.encode()).hexdigest()[:10]
    assert vcpkg.fingerprint(Download(("u",), "f", "ff", ("/elsewhere.patch",), main=True), files) is None


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def test_git_repo_fetches_commits_and_trees_it_is_asked_about(tmp_path):
    remote = tmp_path / "remote"
    (remote / "ports/zlib").mkdir(parents=True)
    (remote / "ports/zlib/portfile.cmake").write_text("old")
    _git("init", "-q", cwd=remote)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=remote)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "1", cwd=remote)
    old_tree = _git("rev-parse", "HEAD:ports/zlib", cwd=remote)
    (remote / "ports/zlib/portfile.cmake").write_text("new")
    (remote / "versions").mkdir()
    (remote / "versions/baseline.json").write_text("{}")
    _git("-c", "user.name=t", "-c", "user.email=t@t", "add", ".", cwd=remote)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "2", cwd=remote)
    head = _git("rev-parse", "HEAD", cwd=remote)
    for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
        _git("config", key, "true", cwd=remote)

    repo = GitRepo(f"file://{remote}", tmp_path / "cache")
    assert repo.show(head, "versions/baseline.json") == b"{}"
    assert repo.tree(old_tree) == {"portfile.cmake": b"old"}  # from an older commit than the one fetched
    assert GitRepo(f"file://{remote}", tmp_path / "cache").show(head, "ports/zlib/portfile.cmake") == b"new"
