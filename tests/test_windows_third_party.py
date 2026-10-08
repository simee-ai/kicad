import hashlib
import json
import tarfile

import pytest

from archives import targz
from kicad_bundle import pe, third_party, vcpkg, windows_third_party
from kicad_bundle.vcpkg import Port, Registry

MICROSOFT = Registry("https://github.com/microsoft/vcpkg", "01e159b519" + "0" * 30)
GIT2_PATCH = b"--- a/x\n+++ b/x\n"
CONFIG = {"vcpkg.json": {"name": "kicad", "overrides": []},
          "vcpkg-configuration.json": {"default-registry": {"kind": "git", "repository": MICROSOFT.url,
                                                            "baseline": MICROSOFT.baseline}}}


def _source(tmp_path, name: str, files: dict[str, bytes]) -> bytes:
    return targz(tmp_path / "upstream" / name, files).read_bytes()


@pytest.fixture
def kicad(tmp_path, monkeypatch):
    """KiCad's bin/ with git2.dll (fingerprinted), zlib1.dll and the MSVC runtime; the registry's ports
    and the upstream sources served locally."""
    git2_src = _source(tmp_path, "git2.tar.gz", {"libgit2-1.9.2/COPYING": b"GPL-2 + linking exception"})
    zlib_src = _source(tmp_path, "zlib.tar.gz", {"zlib-1.3.1/LICENSE": b"zlib", "zlib-1.3.1/zlib.h": b""})
    sha = {name: hashlib.sha512(data).hexdigest() for name, data in (("git2", git2_src), ("zlib", zlib_src))}
    git2_hash = hashlib.sha512((sha["git2"] + hashlib.sha512(GIT2_PATCH).hexdigest()).encode()).hexdigest()[:10]
    ports = {
        "libgit2": Port("libgit2", "1.9.2", 0, MICROSOFT, "2fd041b43a" + "0" * 30, {
            "portfile.cmake": f"vcpkg_from_github(OUT_SOURCE_PATH SOURCE_PATH REPO libgit2/libgit2 "
                              f"REF v${{VERSION}} SHA512 {sha['git2']} PATCHES c-standard.diff)".encode(),
            "c-standard.diff": GIT2_PATCH}),
        "zlib": Port("zlib", "1.3.1", 0, MICROSOFT, "3f05e04b9a" + "0" * 30, {
            "portfile.cmake": f"vcpkg_from_github(OUT_SOURCE_PATH SOURCE_PATH REPO madler/zlib "
                              f"REF v${{VERSION}} SHA512 {sha['zlib']})".encode()})}
    served = {f"{vcpkg.KICAD_SOURCE}/10.0.6/{name}": json.dumps(body).encode() for name, body in CONFIG.items()}
    served["https://github.com/libgit2/libgit2/archive/v1.9.2.tar.gz"] = git2_src
    served["https://github.com/madler/zlib/archive/v1.3.1.tar.gz"] = zlib_src
    monkeypatch.setattr(vcpkg, "port", lambda config, name, repo: ports[name])
    versions = {"zlib1.dll": {"FileVersion": "1.3.1"}, "git2.dll": {"FileVersion": "1.9.2"},
                "vcruntime140.dll": {"CompanyName": "Microsoft Corporation", "FileVersion": "14.44.35211.0"}}
    monkeypatch.setattr(pe, "version_info", lambda path: versions.get(path.name, {}))

    root = tmp_path / "kicad-cli-10.0.6-windows-x86_64"
    binaries = {"kicad-cli.exe": b"kicad", "_eeschema.dll": b"kiface", "kicommon.dll": b"ki",
                "git2.dll": b"\0C:\\vcpkg\\buildtrees\\libgit2\\src\\v1.9.2-" + git2_hash.encode()
                            + b".clean\\src\\util\\errors.c\0",
                "zlib1.dll": b"C:\\vcpkg\\buildtrees\\zlib\\x64-windows-rel\\zlib.pdb\0",
                "vcruntime140.dll": b"D:\\a\\_work\\1\\s\\binaries\\vcruntime140.amd64.pdb"}
    (root / "bin").mkdir(parents=True)
    for name, data in binaries.items():
        (root / "bin" / name).write_bytes(data)
    return {"root": root, "files": sorted((root / "bin").iterdir()), "fetch": served.__getitem__,
            "versions": versions, "cache": tmp_path / "cache"}


def _collect(kicad, files=None):
    return windows_third_party.collect(kicad["root"], files or kicad["files"], "10.0.6", "2026", kicad["cache"],
                                       fetch=kicad["fetch"])


def test_collect_maps_every_dll_to_its_vcpkg_port_and_fetches_its_sources(kicad):
    third = _collect(kicad)
    assert [(c.name, c.version, c.folder) for c in third.components] == [
        ("libgit2", "1.9.2#0", "libgit2-1.9.2_0"), ("zlib", "1.3.1#0", "zlib-1.3.1_0")]
    git2 = third.components[0]
    assert [name for name, _ in git2.sources] == ["libgit2-libgit2-v1.9.2.tar.gz", "port/c-standard.diff",
                                                  "port/portfile.cmake"]
    assert dict(git2.sources)["port/c-standard.diff"].read_bytes() == GIT2_PATCH
    assert third.rows == [
        ("bin/git2.dll", "libgit2 1.9.2#0", "vcpkg port, github.com/microsoft/vcpkg 01e159b519 (tree 2fd041b43a)"),
        ("bin/zlib1.dll", "zlib 1.3.1#0", "vcpkg port, github.com/microsoft/vcpkg 01e159b519 (tree 3f05e04b9a)")]
    assert third.licences == {"libgit2": {"COPYING": b"GPL-2 + linking exception"}, "zlib": {"LICENSE": b"zlib"}}
    assert third.microsoft == [("bin/vcruntime140.dll", "14.44.35211.0")]


def test_collect_fails_on_a_dll_of_unknown_provenance(kicad):
    (kicad["root"] / "bin/mystery.dll").write_bytes(b"no build paths")
    with pytest.raises(RuntimeError, match="mystery.dll"):
        _collect(kicad, [*kicad["files"], kicad["root"] / "bin/mystery.dll"])


def test_collect_fails_when_a_source_folder_hash_does_not_match_the_port(kicad):
    (kicad["root"] / "bin/git2.dll").write_bytes(b"C:\\vcpkg\\buildtrees\\libgit2\\src\\v1.9.2-0123456789.clean\\x")
    with pytest.raises(RuntimeError, match="libgit2.*0123456789"):
        _collect(kicad)


def test_collect_fails_when_a_dlls_version_is_not_the_ports(kicad):
    kicad["versions"]["zlib1.dll"] = {"FileVersion": "1.2.13", "ProductVersion": "1.2.13"}
    with pytest.raises(RuntimeError, match="zlib1.dll.*1.2.13.*1.3.1"):
        _collect(kicad)


def test_collect_accepts_a_dll_whose_product_version_is_the_ports(kicad):
    kicad["versions"]["zlib1.dll"] = {"FileVersion": "62,4,0,0", "ProductVersion": "1.3.1"}
    assert [c.name for c in _collect(kicad).components] == ["libgit2", "zlib"]


def test_collect_fails_on_a_runtime_dll_that_is_not_microsofts(kicad):
    kicad["versions"]["vcruntime140.dll"] = {"CompanyName": "Someone"}
    with pytest.raises(RuntimeError, match="vcruntime140.dll"):
        _collect(kicad)


def test_write_notices_lists_each_file_and_ships_each_ports_licences(kicad):
    third = _collect(kicad)
    windows_third_party.write_notices(third, kicad["root"], "10.0.6", "kicad-cli-10.0.6-windows-x86_64-sources.tar")
    text = (kicad["root"] / "THIRD-PARTY.txt").read_text()
    assert "kicad-cli-10.0.6-windows-x86_64-sources.tar" in text
    assert "bin/git2.dll\tlibgit2 1.9.2#0\tvcpkg port" in text
    assert "(vcruntime140.dll)" in text and "14.44.35211.0" in text
    assert (kicad["root"] / "share/doc/libgit2/COPYING").read_bytes() == b"GPL-2 + linking exception"


def test_sources_go_into_one_archive_folder_per_port(kicad, tmp_path):
    third = _collect(kicad)
    out = third_party.sources_archive(third.components, tmp_path / "out/s.tar")
    with tarfile.open(out) as tar:
        assert "s/zlib-1.3.1_0/madler-zlib-v1.3.1.tar.gz" in tar.getnames()
