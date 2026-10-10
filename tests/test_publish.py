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
    for how in ("KiCad.app/Contents/MacOS/kicad-cli", "bin\\kicad-cli.exe", "Windows 10", "bin/kicad-cli",
                "glibc 2.39"):
        assert how in text
    assert "kicad/kicad:10.0.6" in text  # where the Linux binaries come from
    assert "Linux x86_64 or arm64: `bin/kicad-cli`" in " ".join(text.split())


def test_release_notes_say_where_the_arm64_linux_bundle_comes_from():
    text = " ".join(release_notes("10.0.6", "https://run/1", [], simee_sha="4e18395976" + "0" * 30).split())
    assert "kicad-cli-10.0.6-linux-arm64-sources.tar" in text
    assert "amd64 only" in text


def test_release_notes_say_which_commands_the_bundle_runs():
    # sch (eeschema, cvpcb for ERC), and since simee-kicad#8 fp and pcb (pcbnew)
    text = " ".join(release_notes("10.0.6", "https://run/1", []).split())
    assert "`kicad-cli sch ...`, `fp ...` and `pcb ...`" in text
    assert "gerbers" in text


def test_release_notes_point_at_the_third_party_notices_and_sources():
    text = release_notes("10.0.6", "https://run/1", [])
    assert "THIRD-PARTY.txt" in text
    assert "kicad-cli-10.0.6-linux-x86_64-sources.tar" in text
    assert "kicad-cli-10.0.6-macos-sources.tar" in text
    assert "Contents/Resources/Licenses" in text
    assert "kicad-cli-10.0.6-windows-x86_64-sources.tar" in text
    assert "share\\doc\\<port>" in text


def test_release_notes_of_a_simee_build_say_what_changed_and_where_the_source_is():
    sha = "4e18395976" + "0" * 30
    text = release_notes("10.0.6", "https://run/1", [], simee_sha=sha)
    assert "unmodified" not in text
    assert f"https://github.com/simee-ai/simee-kicad/commit/{sha}" in text
    assert "kicad-cli sch import" in text
    assert "kicad-10.0.6-source.tar.gz" in text
    assert "https://gitlab.com/kicad/code/kicad/-/tags/10.0.6" not in text
    assert "unmodified" in release_notes("10.0.6", "https://run/1", [])
