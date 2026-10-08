import hashlib
import io
import json
import tarfile
import urllib.error

import pytest

import tiny_macho
from kicad_bundle import homebrew
from kicad_bundle.macho import Slice

OLD, NEW = "7AD804A6-C91B-3A8F-81DB-2E0726D3D42E", "11111111-2222-3333-4444-555555555555"
SOURCE = b"glib source"
PATCH = b"--- a\n+++ b\n"
LOCAL_PATCH = b"--- hardcoded\n+++ paths\n"


def _rb(arm64_sonoma: str, version: str = "2.88.3") -> str:
    return f'''class Glib < Formula
  url "https://download.gnome.org/sources/glib/2.88/glib-{version}.tar.xz"
  sha256 "{hashlib.sha256(SOURCE).hexdigest()}"
  license "LGPL-2.1-or-later"

  bottle do
    rebuild 1
    sha256 cellar: :any, arm64_sonoma: "{arm64_sonoma}"
    sha256 sonoma:       "{"0" * 64}"
  end

  patch do
    url "https://example.org/fix.patch"
    sha256 "{hashlib.sha256(PATCH).hexdigest()}"
  end

  patch do
    file "Patches/glib/hardcoded-paths.diff"
  end

  patch :DATA
end
__END__
inline patch
'''


def _bottle(version: str, uuid: str, sbom: bool = True, rb: str | None = None) -> bytes:
    keg = f"glib/{version}"
    sbom_present, sbom = sbom, {"packages": [{"SPDXID": "SPDXRef-Archive-glib-src", "versionInfo": version,
                          "downloadLocation": f"https://download.gnome.org/sources/glib/2.88/glib-{version}.tar.xz",
                          "checksums": [{"algorithm": "SHA256", "checksumValue": hashlib.sha256(SOURCE).hexdigest()}]}]}
    files = {f"{keg}/lib/libglib-2.0.0.dylib": tiny_macho.thin("arm64", uuid),
             f"{keg}/lib/pkgconfig/glib-2.0.pc": b"prefix=x",
             f"{keg}/bin/gdbus": tiny_macho.thin("arm64", "99999999-9999-9999-9999-999999999999"),
             f"{keg}/sbom.spdx.json": json.dumps(sbom).encode(),
             f"{keg}/.brew/glib.rb": (rb or _rb("built-from")).encode()}
    if not sbom_present:
        del files[f"{keg}/sbom.spdx.json"]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeWeb:
    """homebrew-core history: c3 (newest, bottle of 2.88.4 with NEW), c2 (the bump to 2.88.4, which
    still names c1's bottle and has dropped its patch file), c1 (bottle of 2.88.3 with OLD)."""

    def __init__(self):
        self.blobs = {hashlib.sha256(b).hexdigest(): b for b in (_bottle("2.88.4", NEW), _bottle("2.88.3", OLD))}
        new, old = list(self.blobs)
        self.rb = {"c3": _rb(new, "2.88.4"), "c2": _rb(old, "2.88.4"), "c1": _rb(old)}
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url.startswith("https://api.github.com/repos/Homebrew/homebrew-core/commits?path=Formula/g/glib.rb&"):
            assert "until=2026-08-29T15:43:28Z" in url
            return json.dumps([{"sha": c} for c in ("c3", "c2", "c1")]).encode()
        if url.startswith("https://raw.githubusercontent.com/Homebrew/homebrew-core/"):
            commit, path = url.split("/", 6)[5:]
            if path == "Formula/g/glib.rb":
                return self.rb[commit].encode()
            assert path == "Patches/glib/hardcoded-paths.diff"
            if commit != "c1":
                raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
            return LOCAL_PATCH
        if url.startswith("https://ghcr.io/v2/homebrew/core/glib/blobs/sha256:"):
            return self.blobs[url.rsplit(":", 1)[1]]
        if url == "https://download.gnome.org/sources/glib/2.88/glib-2.88.3.tar.xz":
            return SOURCE
        if url == "https://example.org/fix.patch":
            return PATCH
        raise AssertionError(url)


def test_formula_maps_bundled_library_names_to_homebrew_formulae():
    assert homebrew.formula("libglib-2.0.0.dylib") == "glib"
    assert homebrew.formula("libicuuc.78.3.dylib") == "icu4c@78"
    assert homebrew.formula("libssl.3.dylib") == "openssl@3"
    assert homebrew.formula("libxcb-render.0.0.0.dylib") == "libxcb"
    assert homebrew.formula("libabsl_base.2601.0.0.dylib") == "abseil"
    assert homebrew.formula("libkicommon.10.0.6.dylib") is None


def test_formula_path_follows_homebrew_cores_sharding():
    assert homebrew.formula_path("glib") == "Formula/g/glib.rb"
    assert homebrew.formula_path("libx11") == "Formula/lib/libx11.rb"
    assert homebrew.formula_path("llhttp") == "Formula/l/llhttp.rb"
    assert homebrew.formula_path("openssl@3") == "Formula/o/openssl@3.rb"


def test_bottle_tag_names_the_macos_a_library_was_built_for():
    assert homebrew.bottle_tag("arm64", (14, 0)) == "arm64_sonoma"
    assert homebrew.bottle_tag("x86_64", (14, 0)) == "sonoma"
    assert homebrew.bottle_tag("arm64", (26, 0)) == "arm64_tahoe"
    with pytest.raises(RuntimeError, match="macOS 99"):
        homebrew.bottle_tag("arm64", (99, 0))


def test_bottle_shas_and_patches_come_from_the_formula():
    rb = _rb("a" * 64)
    assert homebrew.bottle_shas(rb) == {"arm64_sonoma": "a" * 64, "sonoma": "0" * 64}
    assert homebrew.patches(rb) == [("https://example.org/fix.patch", hashlib.sha256(PATCH).hexdigest()),
                                    ("Patches/glib/hardcoded-paths.diff", None)]


def test_match_walks_back_to_the_bottle_holding_the_libraries_uuids(tmp_path):
    web = FakeWeb()
    libs = {"libglib-2.0.0.dylib": Slice(OLD, (14, 0))}
    bottle = homebrew.match("glib", "arm64", libs, "2026-08-29T15:43:28Z", tmp_path, web)
    # c1 added the bottle; c2 still names it but its formula is already 2.88.4's
    assert (bottle.formula, bottle.version, bottle.tag, bottle.commit) == ("glib", "2.88.3", "arm64_sonoma", "c1")
    assert bottle.sha256 == hashlib.sha256(_bottle("2.88.3", OLD)).hexdigest()
    blobs = [u for u in web.calls if "ghcr.io" in u]
    assert len(blobs) == 2  # the 2.88.4 bottle, then 2.88.3 once although c2 and c1 both name it

    web.calls.clear()
    assert homebrew.match("glib", "arm64", libs, "2026-08-29T15:43:28Z", tmp_path, web) == bottle
    assert not [u for u in web.calls if "ghcr.io" in u]  # the bottles' UUIDs are cached


def test_match_fails_when_no_bottle_holds_the_library(tmp_path):
    libs = {"libglib-2.0.0.dylib": Slice("DEADBEEF-0000-0000-0000-000000000000", (14, 0))}
    with pytest.raises(RuntimeError, match="glib.*no arm64_sonoma bottle"):
        homebrew.match("glib", "arm64", libs, "2026-08-29T15:43:28Z", tmp_path, FakeWeb())


def test_sources_are_the_sbom_archive_the_patches_and_the_formula(tmp_path):
    web = FakeWeb()
    bottle = homebrew.match("glib", "arm64", {"libglib-2.0.0.dylib": Slice(OLD, (14, 0))},
                            "2026-08-29T15:43:28Z", tmp_path / "bottles", web)
    files = homebrew.sources(bottle, tmp_path / "sources", web)
    assert {name: path.read_bytes() for name, path in files} == {
        "glib-2.88.3.tar.xz": SOURCE, "patches/01-fix.patch": PATCH,
        "patches/02-hardcoded-paths.diff": LOCAL_PATCH, "glib.rb": _rb("built-from").encode()}
    assert "https://raw.githubusercontent.com/Homebrew/homebrew-core/c1/Patches/glib/hardcoded-paths.diff" in web.calls


# A formula whose source is in a `stable do` block, next to a `head do` one and a resource.
STABLE_RB = f'''class Glib < Formula
  desc "Core application library for C"
  license "LGPL-2.1-or-later"

  stable do
    url "https://download.gnome.org/sources/glib/2.88/glib-2.88.3.tar.xz"
    sha256 "{hashlib.sha256(SOURCE).hexdigest()}"

    patch do
      url "https://example.org/fix.patch"
      sha256 "{hashlib.sha256(PATCH).hexdigest()}"
    end
  end

  head do
    url "https://gitlab.gnome.org/GNOME/glib.git", branch: "main"
  end

  resource "packaging" do
    url "https://example.org/packaging.tar.gz"
    sha256 "{"1" * 64}"
  end
end
'''


def test_source_comes_from_the_formula_when_the_bottle_has_no_sbom():
    assert homebrew.formula_source(_rb("x" * 64)) == (
        "https://download.gnome.org/sources/glib/2.88/glib-2.88.3.tar.xz", hashlib.sha256(SOURCE).hexdigest())
    assert homebrew.formula_source(STABLE_RB) == (
        "https://download.gnome.org/sources/glib/2.88/glib-2.88.3.tar.xz", hashlib.sha256(SOURCE).hexdigest())
    with pytest.raises(RuntimeError, match="no source url"):
        homebrew.formula_source('class Glib < Formula\n  url "https://x.org/glib.git", tag: "2.88.3"\nend\n')


@pytest.mark.parametrize("rb", [None, STABLE_RB])
def test_sources_of_a_bottle_without_an_sbom_use_its_formulas_url(tmp_path, rb):
    web = FakeWeb()
    web.blobs = {hashlib.sha256(b).hexdigest(): b for b in [_bottle("2.88.3", OLD, sbom=False, rb=rb)]}
    web.rb = {c: _rb(next(iter(web.blobs))) for c in ("c3", "c2", "c1")}
    bottle = homebrew.match("glib", "arm64", {"libglib-2.0.0.dylib": Slice(OLD, (14, 0))},
                            "2026-08-29T15:43:28Z", tmp_path / "bottles", web)
    assert not (bottle.info / "sbom.spdx.json").exists()
    files = dict(homebrew.sources(bottle, tmp_path / "sources", web))
    assert files["glib-2.88.3.tar.xz"].read_bytes() == SOURCE
    assert files["patches/01-fix.patch"].read_bytes() == PATCH


def test_archive_keeps_the_bottle_for_pouring(tmp_path):
    web = FakeWeb()
    bottle = homebrew.match("glib", "arm64", {"libglib-2.0.0.dylib": Slice(OLD, (14, 0))},
                            "2026-08-29T15:43:28Z", tmp_path, web)
    path = homebrew.archive(bottle, tmp_path, web)
    assert path.read_bytes() == _bottle("2.88.3", OLD)
    web.calls.clear()
    assert homebrew.archive(bottle, tmp_path, web) == path
    assert not web.calls


def test_bottle_at_is_the_newest_bottle_for_the_tag_before_a_date(tmp_path):
    bottle = homebrew.bottle_at("glib", "arm64_sonoma", "2026-08-29T15:43:28Z", tmp_path, FakeWeb())
    assert (bottle.version, bottle.tag, bottle.commit) == ("2.88.4", "arm64_sonoma", "c3")
