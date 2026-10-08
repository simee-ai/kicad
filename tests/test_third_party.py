import tarfile

import pytest

from archives import targz, zip_
from kicad_bundle import third_party
from kicad_bundle.third_party import Component


def test_licences_are_the_licence_files_near_the_top_of_the_source_and_its_submodules(tmp_path):
    archive = targz(tmp_path / "wx.tar.gz", {
        "wx-1/LICENSE": b"top", "wx-1/COPYING-LGPL-2.1": b"lgpl", "wx-1/docs/FTL.TXT": b"ftl",
        "wx-1/docs/GPLv2.TXT": b"gpl", "wx-1/LICENSES/Apache-2.0.txt": b"apache", "wx-1/README": b"no",
        "wx-1/src/deep/LICENSE": b"too deep", "wx-1/.gitmodules": b'[submodule "png"]\n\tpath = src/png\n',
        "wx-1/src/png/LICENSE": b"png", "wx-1/src/png/contrib/x/LICENSE": b"too deep"})
    assert third_party.licences(archive) == {
        "LICENSE": b"top", "COPYING-LGPL-2.1": b"lgpl", "docs/FTL.TXT": b"ftl", "docs/GPLv2.TXT": b"gpl",
        "LICENSES/Apache-2.0.txt": b"apache", "src/png/LICENSE": b"png"}


def test_licences_reads_zip_archives_too(tmp_path):
    archive = zip_(tmp_path / "pcre-8.45.zip", {"pcre-8.45/LICENCE": b"BSD", "pcre-8.45/pcre.h": b"",
                                                 "pcre-8.45/doc/": b""})
    assert third_party.licences(archive) == {"LICENCE": b"BSD"}


def test_licences_refuses_a_source_without_any(tmp_path):
    with pytest.raises(RuntimeError, match="no licence"):
        third_party.licences(targz(tmp_path / "x.tar.gz", {"x-1/README": b""}))


def test_sources_archive_holds_one_folder_per_component(tmp_path):
    (tmp_path / "a.tar.xz").write_bytes(b"src")
    (tmp_path / "p.diff").write_bytes(b"patch")
    glib = Component("glib", "2.88.3", "glib-2.88.3", tmp_path / "a.tar.xz",
                     (("glib-2.88.3.tar.xz", tmp_path / "a.tar.xz"), ("patches/01-p.diff", tmp_path / "p.diff")))
    out = third_party.sources_archive([glib], tmp_path / "out/kicad-cli-10.0.6-macos-sources.tar")
    with tarfile.open(out) as tar:
        assert sorted(tar.getnames()) == [
            "kicad-cli-10.0.6-macos-sources", "kicad-cli-10.0.6-macos-sources/glib-2.88.3",
            "kicad-cli-10.0.6-macos-sources/glib-2.88.3/glib-2.88.3.tar.xz",
            "kicad-cli-10.0.6-macos-sources/glib-2.88.3/patches/01-p.diff"]
