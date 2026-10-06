from kicad_bundle.publish import next_tag, release_notes, sha256sums


def test_next_tag_counts_builds_per_kicad_version():
    assert next_tag("10.0.6", []) == "cli-10.0.6-1"
    assert next_tag("10.0.6", ["cli-10.0.6-1", "cli-10.0.5-3", "10.0.6"]) == "cli-10.0.6-2"
    assert next_tag("10.0.6", ["cli-10.0.6-1", "cli-10.0.6-4"]) == "cli-10.0.6-5"


def test_sha256sums_lists_every_asset(tmp_path):
    (tmp_path / "b.zip").write_bytes(b"b")
    (tmp_path / "a.tar.gz").write_bytes(b"a")
    lines = sha256sums(tmp_path).splitlines()
    assert [l.split("  ")[1] for l in lines] == ["a.tar.gz", "b.zip"]
    assert lines[0].split("  ")[0] == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"


def test_release_notes_name_the_source_and_assets():
    text = release_notes("10.0.6", "https://run/1", ["kicad-cli-10.0.6-macos-arm64.tar.gz"])
    assert "kicad-10.0.6-source.tar.gz" in text
    assert "https://gitlab.com/kicad/code/kicad/-/tags/10.0.6" in text
    assert "kicad-cli-10.0.6-macos-arm64.tar.gz" in text
    assert "https://run/1" in text


def test_release_notes_say_how_to_run_each_platform():
    text = release_notes("10.0.6", "https://run/1", [])
    for how in ("KiCad.app/Contents/MacOS/kicad-cli", "bin\\kicad-cli.exe", "bin/kicad-cli", "glibc 2.39"):
        assert how in text
    assert "kicad/kicad:10.0.6" in text  # where the Linux binaries come from


def test_release_notes_point_at_the_third_party_notices_and_sources():
    text = release_notes("10.0.6", "https://run/1", [])
    assert "THIRD-PARTY.txt" in text
    assert "kicad-cli-10.0.6-linux-x86_64-sources.tar" in text
