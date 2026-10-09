import hashlib
import json
import tarfile

import pytest

from kicad_bundle import debian
from kicad_bundle.debian import Package

STATUS = """\
Package: libcairo2
Status: install ok installed
Architecture: amd64
Multi-Arch: same
Source: cairo
Version: 1.18.4-1
Description: Cairo 2D vector graphics library
 a continuation line: with a colon

Package: libgmp10
Status: install ok installed
Source: gmp (2:6.3.0+dfsg-3)
Version: 2:6.3.0+dfsg-3+b1

Package: zlib1g
Status: install ok installed
Version: 1:1.3.dfsg+really1.3.1-1

Package: gone
Status: deinstall ok config-files
Version: 1.0
"""


def test_packages_reads_installed_packages_and_their_source_version():
    pkgs = debian.packages(STATUS)
    assert pkgs == {
        "libcairo2": Package("libcairo2", "1.18.4-1", "cairo", "1.18.4-1"),
        "libgmp10": Package("libgmp10", "2:6.3.0+dfsg-3+b1", "gmp", "2:6.3.0+dfsg-3"),  # binNMU
        "zlib1g": Package("zlib1g", "1:1.3.dfsg+really1.3.1-1", "zlib1g", "1:1.3.dfsg+really1.3.1-1"),
    }


def test_packages_read_the_sources_a_package_was_built_using():
    """A package built from another package's source (Debian's Built-Using) needs that source too."""
    pkgs = debian.packages("Package: g++-mingw-w64-x86-64-win32\nStatus: install ok installed\n"
                           "Source: gcc-mingw-w64 (25.2)\nVersion: 12.2.0-14+25.2\n"
                           "Built-Using: gcc-12 (= 12.2.0-14), mingw-w64 (= 10.0.0-3)\n")
    assert pkgs["g++-mingw-w64-x86-64-win32"].built_using == (("gcc-12", "12.2.0-14"), ("mingw-w64", "10.0.0-3"))
    assert debian.packages(STATUS)["libcairo2"].built_using == ()


def _dpkg(rootfs):
    info = rootfs / debian.DPKG_INFO
    info.mkdir(parents=True)
    (rootfs / debian.DPKG_STATUS).write_text(STATUS)
    (info / "libcairo2:amd64.list").write_text(
        "/.\n/usr\n/usr/lib/x86_64-linux-gnu/libcairo.so.2.11804.4\n/usr/lib/x86_64-linux-gnu/libcairo.so.2\n")
    (info / "libgmp10:amd64.list").write_text("/lib/x86_64-linux-gnu/libgmp.so.10.5.0\n")  # pre-merged-/usr path
    (info / "zlib1g.list").write_text("/usr/lib/x86_64-linux-gnu/libz.so.1.3.1\n")


def test_provenance_maps_files_to_their_packages_and_lists_the_rest(tmp_path):
    _dpkg(tmp_path)
    lib = tmp_path / "usr/lib/x86_64-linux-gnu"
    files = [lib / "libcairo.so.2.11804.4", lib / "libgmp.so.10.5.0", lib / "libkicommon.so.10.0.6"]
    owned, unowned = debian.provenance(tmp_path, files)
    assert owned == {files[0]: Package("libcairo2", "1.18.4-1", "cairo", "1.18.4-1"),
                     files[1]: Package("libgmp10", "2:6.3.0+dfsg-3+b1", "gmp", "2:6.3.0+dfsg-3")}
    assert unowned == [files[2]]


def test_srcfiles_url_quotes_the_version():
    assert debian.srcfiles_url("gmp", "2:6.3.0+dfsg-3") == \
        "https://snapshot.debian.org/mr/package/gmp/2%3A6.3.0%2Bdfsg-3/srcfiles?fileinfo=1"


def test_source_files_names_each_file_by_its_hash():
    reply = {"result": [{"hash": "aa"}, {"hash": "bb"}],
             "fileinfo": {"aa": [{"name": "cairo_1.18.4.orig.tar.xz", "archive_name": "debian-debug"},
                                 {"name": "cairo_1.18.4.orig.tar.xz", "archive_name": "debian"}],
                          "bb": [{"name": "cairo_1.18.4-1.dsc", "archive_name": "debian"}]}}
    assert debian.source_files(reply) == [("cairo_1.18.4-1.dsc", "bb"), ("cairo_1.18.4.orig.tar.xz", "aa")]


def _fake_snapshot(files: dict[str, bytes]):
    """A fetch() serving one source package's srcfiles reply and its files by sha1."""
    by_hash = {hashlib.sha1(data).hexdigest(): (name, data) for name, data in files.items()}
    reply = {"result": [{"hash": h} for h in by_hash],
             "fileinfo": {h: [{"name": name, "archive_name": "debian"}] for h, (name, _) in by_hash.items()}}
    calls = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.endswith("srcfiles?fileinfo=1"):
            return json.dumps(reply).encode()
        return by_hash[url.rsplit("/", 1)[1]][1]

    return fetch, calls


def test_sources_archive_holds_each_source_package_once_and_caches_files(tmp_path):
    fetch, calls = _fake_snapshot({"cairo_1.18.4-1.dsc": b"dsc", "cairo_1.18.4.orig.tar.xz": b"orig"})
    srcs = {("cairo", "1.18.4-1")}
    out = tmp_path / "out" / "bundle-sources.tar"
    debian.sources_archive(srcs, out, tmp_path / "cache", fetch=fetch)
    with tarfile.open(out) as tar:
        assert sorted(tar.getnames()) == ["bundle-sources", "bundle-sources/cairo_1.18.4-1",
                                          "bundle-sources/cairo_1.18.4-1/cairo_1.18.4-1.dsc",
                                          "bundle-sources/cairo_1.18.4-1/cairo_1.18.4.orig.tar.xz"]
        assert tar.extractfile("bundle-sources/cairo_1.18.4-1/cairo_1.18.4-1.dsc").read() == b"dsc"
    downloads = len(calls)
    debian.sources_archive(srcs, tmp_path / "again.tar", tmp_path / "cache", fetch=fetch)
    assert len(calls) == downloads + 1  # only the srcfiles listing again


def test_sources_archive_rejects_a_corrupt_download(tmp_path):
    fetch, _ = _fake_snapshot({"cairo_1.18.4-1.dsc": b"dsc"})

    def corrupt(url: str) -> bytes:
        return fetch(url) if url.endswith("fileinfo=1") else b"tampered"

    with pytest.raises(RuntimeError, match="sha1"):
        debian.sources_archive({("cairo", "1.18.4-1")}, tmp_path / "s.tar", tmp_path / "cache", fetch=corrupt)
    assert not list((tmp_path / "cache").rglob("*.dsc"))
