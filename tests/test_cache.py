import hashlib
import json
import os
import time

from kicad_bundle import cache, debian

DAY = 24 * 3600


def _file(path, age_days=0.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    t = time.time() - age_days * DAY
    os.utime(path, (t, t))
    return path


def test_prune_keeps_installers_of_the_built_and_the_newest_other_version(tmp_path):
    for v in ["9.0.8", "10.0.5", "10.0.6", "10.0.10"]:
        _file(tmp_path / v / f"kicad-{v}.dmg")
    cache.prune(tmp_path, built="10.0.5")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["10.0.10", "10.0.5"]


def test_prune_keeps_the_newest_two_when_the_newest_was_built(tmp_path):
    for v in ["9.0.8", "10.0.5", "10.0.6"]:
        _file(tmp_path / v / f"kicad-{v}.dmg")
    cache.prune(tmp_path, built="10.0.6")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["10.0.5", "10.0.6"]


def test_prune_leaves_what_it_does_not_own(tmp_path):
    _file(tmp_path / "notes" / "keep.txt")
    _file(tmp_path / "README", age_days=400)
    cache.prune(tmp_path, built="10.0.6")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["README", "notes"]


def test_prune_drops_debian_sources_no_recent_build_used(tmp_path):
    sources = tmp_path / cache.DEBIAN_SOURCES
    fresh = _file(sources / "ab" / "ab12" / "cairo_1.18.4-1.dsc", age_days=cache.SOURCES_MAX_AGE_DAYS - 1)
    stale = _file(sources / "cd" / "cd34" / "gmp_6.3.0.dsc", age_days=cache.SOURCES_MAX_AGE_DAYS + 1)
    _file(sources / "cd" / "cd56" / "zlib_1.3.dsc")
    cache.prune(tmp_path, built="10.0.6")
    assert fresh.exists() and not stale.exists()
    assert not stale.parent.exists()  # its empty sha1 folder goes too
    assert (sources / "cd").exists()


def test_prune_drops_macos_sources_and_bottle_records_no_recent_build_used(tmp_path):
    keep = cache.SOURCES_MAX_AGE_DAYS - 1
    fresh = [_file(tmp_path / cache.MACOS_SOURCES / "git" / "wxWidgets-f9c61658f683.tar.gz", age_days=keep),
             _file(tmp_path / cache.HOMEBREW_BOTTLES / "ab12" / "uuids.json", age_days=keep)]
    stale = [_file(tmp_path / cache.MACOS_SOURCES / "ab" / "ab12" / "glib-2.88.2.tar.xz", age_days=keep + 2),
             _file(tmp_path / cache.HOMEBREW_BOTTLES / "cd34" / "uuids.json", age_days=keep + 2)]
    cache.prune(tmp_path, built="10.0.6")
    assert all(f.exists() for f in fresh) and not any(f.parent.exists() for f in stale)


def test_reusing_a_bottle_record_marks_all_of_it_used(tmp_path):
    from kicad_bundle import homebrew

    info = tmp_path / "ab12"
    for name in ("uuids.json", "version", "formula.rb", "sbom.spdx.json"):
        os.utime(_file(info / name), (0, 0))
    homebrew._bottle_info("glib", "ab12", tmp_path, fetch=None)
    assert all(f.stat().st_mtime > time.time() - DAY for f in info.iterdir())


def test_reusing_a_cached_debian_source_marks_it_used(tmp_path):
    data = b"dsc"
    sha1 = hashlib.sha1(data).hexdigest()
    reply = {"result": [{"hash": sha1}], "fileinfo": {sha1: [{"name": "cairo_1.18.4-1.dsc"}]}}

    def fetch(url):
        return json.dumps(reply).encode() if url.endswith("fileinfo=1") else data

    sources = tmp_path / cache.DEBIAN_SOURCES
    old = _file(sources / sha1[:2] / sha1 / "cairo_1.18.4-1.dsc", age_days=cache.SOURCES_MAX_AGE_DAYS + 1)
    old.write_bytes(data)
    os.utime(old, (0, 0))
    debian.sources_archive({("cairo", "1.18.4-1")}, tmp_path / "s.tar", sources, fetch=fetch)
    cache.prune(tmp_path, built="10.0.6")
    assert old.exists()


def test_kicad_bundle_prunes_the_cache_after_a_build(tmp_path, monkeypatch):
    from kicad_bundle import cli

    for v in ["9.0.8", "10.0.5", "10.0.6"]:
        _file(tmp_path / "cache" / v / f"kicad-{v}.dmg")
    built = _file(tmp_path / "dist" / "kicad-cli.tar.gz")
    monkeypatch.setitem(cli.PACKAGERS, "macos", lambda *args: [built])
    cli.main(["--kicad-version", "10.0.6", "--platform", "macos", "--cache", str(tmp_path / "cache")])
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == ["10.0.5", "10.0.6"]
