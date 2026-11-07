import gzip
import hashlib
import io
import json
import tarfile
import urllib.error
from urllib.parse import urlparse

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


def _bottle(version: str, uuid: str, sbom: bool = True, rb: str | None = None, links: tuple[str, ...] | None = None,
            dylib_version: str = "8803.0.0") -> bytes:
    """links: what its libglib links (then it has an LC_ID_DYLIB of dylib_version too)."""
    keg = f"glib/{version}"
    lib = tiny_macho.thin("arm64", uuid) if links is None else tiny_macho.thin(
        "arm64", uuid, (15, 0), ("@@HOMEBREW_PREFIX@@/opt/glib/lib/libglib-2.0.0.dylib", dylib_version), links)
    sbom_present, sbom = sbom, {"packages": [{"SPDXID": "SPDXRef-Archive-glib-src", "versionInfo": version,
                          "downloadLocation": f"https://download.gnome.org/sources/glib/2.88/glib-{version.split('_')[0]}.tar.xz",
                          "checksums": [{"algorithm": "SHA256", "checksumValue": hashlib.sha256(SOURCE).hexdigest()}]}]}
    files = {f"{keg}/lib/libglib-2.0.0.dylib": lib,
             f"{keg}/lib/pkgconfig/glib-2.0.pc": b"prefix=x",
             f"{keg}/bin/gdbus": tiny_macho.thin("arm64", "99999999-9999-9999-9999-999999999999"),
             f"{keg}/sbom.spdx.json": json.dumps(sbom).encode(),
             f"{keg}/.brew/glib.rb": (rb or _rb("built-from")).encode()}
    if not sbom_present:
        del files[f"{keg}/sbom.spdx.json"]
    buf = io.BytesIO()
    # mtime=0: "w:gz" would stamp the gzip header with the time it was built
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_a_fake_bottle_is_the_same_bytes_whenever_it_is_built(monkeypatch):
    # tests hash a bottle built again later; a gzip header holding the build time broke that across a second boundary
    first = _bottle("2.88.3", OLD)
    monkeypatch.setattr("time.time", lambda: 2_000_000_000.0)
    assert _bottle("2.88.3", OLD) == first


class FakeWeb:
    """homebrew-core history: c3 (newest, bottle of 2.88.4 with NEW), c2 (the bump to 2.88.4, which
    still names c1's bottle and has dropped its patch file), c1 (bottle of 2.88.3 with OLD)."""

    def __init__(self):
        self.blobs = {hashlib.sha256(b).hexdigest(): b for b in (_bottle("2.88.4", NEW), _bottle("2.88.3", OLD))}
        new, old = list(self.blobs)
        self.rb = {"c3": _rb(new, "2.88.4"), "c2": _rb(old, "2.88.4"), "c1": _rb(old)}
        self.history = ["c3", "c2", "c1"]
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if url.startswith("https://api.github.com/repos/Homebrew/homebrew-core/commits?path=Formula/g/glib.rb&"):
            assert "until=2026-08-29T15:43:28Z" in url
            return json.dumps([{"sha": c} for c in self.history]).encode()
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
    assert homebrew.formula("libTKernel.7.9.3.dylib") == "opencascade"  # pcbnew's (simee-kicad#8)
    assert homebrew.formula("libTKDESTEP.7.9.3.dylib") == "opencascade"
    assert homebrew.formula("libtbb.12.19.dylib") == "tbb"
    assert homebrew.formula("libtbbmalloc.2.19.dylib") == "tbb"
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
    blobs = [u for u in web.calls if urlparse(u).hostname == "ghcr.io"]
    assert len(blobs) == 2  # the 2.88.4 bottle, then 2.88.3 once although c2 and c1 both name it

    web.calls.clear()
    assert homebrew.match("glib", "arm64", libs, "2026-08-29T15:43:28Z", tmp_path, web) == bottle
    assert not [u for u in web.calls if urlparse(u).hostname == "ghcr.io"]  # the bottles' UUIDs are cached


def test_match_reaches_a_bottle_from_a_build_machines_months_old_homebrew(tmp_path):
    # KiCad 10.0.7-rc2 (October 2026) shipped glib 2.86.3, bottled in December 2025: ten bottles back
    web = FakeWeb()
    newer = {f"n{i}": _bottle(f"2.90.{i}", f"{i:08d}-0000-0000-0000-000000000000") for i in range(10)}
    for commit, blob in newer.items():
        sha = hashlib.sha256(blob).hexdigest()
        web.blobs[sha], web.rb[commit] = blob, _rb(sha, "2.90.0")
    history = [*newer, "c1"]
    web_history = web.__call__

    def fetch(url: str) -> bytes:
        if url.startswith("https://api.github.com/repos/Homebrew/homebrew-core/commits?"):
            web.calls.append(url)
            return json.dumps([{"sha": c} for c in history]).encode()
        return web_history(url)

    bottle = homebrew.match("glib", "arm64", {"libglib-2.0.0.dylib": Slice(OLD, (14, 0))},
                            "2026-08-29T15:43:28Z", tmp_path, fetch)
    assert (bottle.version, bottle.commit) == ("2.88.3", "c1")


def test_match_fails_when_no_bottle_holds_the_library(tmp_path):
    libs = {"libglib-2.0.0.dylib": Slice("DEADBEEF-0000-0000-0000-000000000000", (14, 0))}
    with pytest.raises(RuntimeError, match="glib.*no arm64_sonoma bottle"):
        homebrew.match("glib", "arm64", libs, "2026-08-29T15:43:28Z", tmp_path, FakeWeb())


def _no_intel_bottles() -> FakeWeb:
    """Homebrew stopped bottling for Intel Macs in September 2026: the formulae have no sonoma bottle."""
    web = FakeWeb()
    web.rb = {c: rb.replace(f'    sha256 sonoma:       "{"0" * 64}"\n', "") for c, rb in web.rb.items()}
    return web


def test_a_library_no_bottle_holds_comes_from_its_keg_built_from_source(tmp_path):
    # KiCad 10.0.7's x86_64 openssl@3: Homebrew built 3.6.5 from source on KiCad's Intel build machine,
    # which left the keg's path in the library (OpenSSL's compiled-in ENGINESDIR)
    web = _no_intel_bottles()
    libs = {"libglib-2.0.0.dylib": Slice("DEADBEEF-0000-0000-0000-000000000000", (14, 0),
                                         frozenset({("glib", "2.88.3"), ("pcre2", "10.47")}))}
    bottle = homebrew.match("glib", "x86_64", libs, "2026-08-29T15:43:28Z", tmp_path / "bottles", web)
    # the same keg version's bottle for the other architecture stands in for its formula, source and headers
    assert (bottle.version, bottle.tag, bottle.built_for, bottle.commit) == ("2.88.3", "arm64_sonoma", "sonoma", "c1")
    assert bottle.sha256 == hashlib.sha256(_bottle("2.88.3", OLD)).hexdigest()
    files = dict(homebrew.sources(bottle, tmp_path / "sources", web))
    assert files["glib-2.88.3.tar.xz"].read_bytes() == SOURCE
    assert files["patches/02-hardcoded-paths.diff"].read_bytes() == LOCAL_PATCH


def test_a_library_no_bottle_holds_that_names_no_keg_fails(tmp_path):
    libs = {"libglib-2.0.0.dylib": Slice("DEADBEEF-0000-0000-0000-000000000000", (14, 0))}
    with pytest.raises(RuntimeError, match="no sonoma bottle .* name no glib keg"):
        homebrew.match("glib", "x86_64", libs, "2026-08-29T15:43:28Z", tmp_path, _no_intel_bottles())


SECURE_TRANSPORT = ("/System/Library/Frameworks/Security.framework/Versions/A/Security",
                    "/System/Library/Frameworks/CoreFoundation.framework/Versions/A/CoreFoundation", "/usr/lib/libz.1.dylib")
OPENSSL3 = ("@@HOMEBREW_PREFIX@@/opt/openssl@3/lib/libssl.3.dylib",
            "@@HOMEBREW_PREFIX@@/opt/openssl@3/lib/libcrypto.3.dylib", "/usr/lib/libz.1.dylib")
OPENSSL4 = ("@@HOMEBREW_PREFIX@@/opt/openssl@4/lib/libssl.4.dylib",
            "@@HOMEBREW_PREFIX@@/opt/openssl@4/lib/libcrypto.4.dylib", "/usr/lib/libz.1.dylib")


def _revisions(links_1: tuple[str, ...] = OPENSSL3) -> FakeWeb:
    """KiCad 10.0.8's libgit2 (simee-kicad#58), as glib: three builds of one version before the release, each
    formula revision linking something else (2.88.3: Security.framework, _1: openssl@3, _2: openssl@4), then
    older versions. Only Apple silicon bottles (Homebrew bottles no Intel tags since September 2026);
    each revision bump still names the previous bottle until Homebrew's "update bottle" commit."""
    web = FakeWeb()
    kegs = {"2.88.3_2": OPENSSL4, "2.88.3_1": links_1, "2.88.3": SECURE_TRANSPORT,
            "2.88.2": SECURE_TRANSPORT, "2.88.1": SECURE_TRANSPORT}
    shas = {}
    web.blobs = {}
    for i, (keg, links) in enumerate(kegs.items()):
        version = "8802.0.0" if keg == "2.88.2" else "8801.0.0" if keg == "2.88.1" else "8803.0.0"
        blob = _bottle(keg, f"{i:08d}-0000-0000-0000-000000000000", rb=f"formula {keg}\n", links=links,
                       dylib_version=version)
        shas[keg] = hashlib.sha256(blob).hexdigest()
        web.blobs[shas[keg]] = blob
    named = {"h7": "2.88.3_2", "h6": "2.88.3_1", "h5": "2.88.3_1", "h4": "2.88.3", "h3": "2.88.3",
             "h2": "2.88.2", "h1": "2.88.2", "h0": "2.88.1"}
    web.rb = {c: _rb(shas[keg]).replace("arm64_sonoma", "arm64_sequoia").replace(f'    sha256 sonoma:       "{"0" * 64}"\n', "")
              for c, keg in named.items()}
    web.history = list(named)
    return web


def _libgit2_like(links: tuple[str, ...]) -> dict[str, Slice]:
    """KiCad's x86_64 slice, built from source for macOS 15, relinked to its Frameworks, naming no keg."""
    return {"libglib-2.0.0.dylib": Slice("DEADBEEF-0000-0000-0000-000000000000", (15, 0), version="8803.0.0",
                                         dylibs=frozenset(link.rsplit("/", 1)[1] for link in links))}


@pytest.mark.parametrize("links, keg, commit", [(OPENSSL4, "2.88.3_2", "h7"), (OPENSSL3, "2.88.3_1", "h5"),
                                                 (SECURE_TRANSPORT, "2.88.3", "h3")])
def test_a_keg_built_from_source_that_names_no_keg_is_the_revision_linking_what_it_links(tmp_path, links, keg, commit):
    web = _revisions()
    bottle = homebrew.match("glib", "x86_64", _libgit2_like(links), "2026-08-29T15:43:28Z", tmp_path / "bottles", web)
    assert (bottle.version, bottle.tag, bottle.built_for, bottle.commit) == (keg, "arm64_sequoia", "sequoia", commit)
    files = dict(homebrew.sources(bottle, tmp_path / "sources", web))
    assert files["glib.rb"].read_text() == f"formula {keg}\n"  # that revision's formula for the notices
    # the walk stops at the first older version that doesn't match: 2.88.1's bottle is never fetched
    oldest = next(s for s, b in web.blobs.items() if "glib/2.88.1/" in _members(b))
    assert not [u for u in web.calls if u.endswith(oldest)]


def _members(blob: bytes) -> str:
    with tarfile.open(fileobj=io.BytesIO(blob)) as tar:
        return " ".join(tar.getnames())


def test_a_keg_built_from_source_that_names_no_keg_and_matches_two_revisions_fails(tmp_path):
    web = _revisions(links_1=OPENSSL4)  # _1 and _2 link the same: nothing in the image tells them apart
    with pytest.raises(RuntimeError, match=r"glib .*sequoia.* builds of glib \['2\.88\.3_1', '2\.88\.3_2'\]"):
        homebrew.match("glib", "x86_64", _libgit2_like(OPENSSL4), "2026-08-29T15:43:28Z", tmp_path, web)


def test_a_keg_built_from_source_that_names_no_keg_and_matches_no_revision_fails(tmp_path):
    links = ("@rpath/libssl.5.dylib", "@rpath/libcrypto.5.dylib", "/usr/lib/libz.1.dylib")
    with pytest.raises(RuntimeError, match="no sequoia bottle .* name no glib keg"):
        homebrew.match("glib", "x86_64", _libgit2_like(links), "2026-08-29T15:43:28Z", tmp_path, _revisions())


def test_a_keg_built_from_source_whose_version_no_bottle_has_fails(tmp_path):
    libs = {"libglib-2.0.0.dylib": Slice("DEADBEEF-0000-0000-0000-000000000000", (14, 0),
                                         frozenset({("glib", "2.87.0")}))}
    with pytest.raises(RuntimeError, match="glib 2.87.0 .*built from source.* no bottle of that version"):
        homebrew.match("glib", "x86_64", libs, "2026-08-29T15:43:28Z", tmp_path, _no_intel_bottles())


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
