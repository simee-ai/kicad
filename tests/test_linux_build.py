from datetime import datetime, timezone
from pathlib import Path

import pytest

from kicad_bundle import linux_build

DIGEST = "kicad/kicad@sha256:" + "ab" * 32


def test_snapshot_stamp_is_utc_to_the_second():
    when = datetime(2026, 9, 22, 12, 55, 49, 700000, tzinfo=timezone.utc).timestamp()
    assert linux_build.snapshot_stamp(when) == "20260922T125549Z"


def test_dockerfile_builds_on_the_official_image_with_its_packages_held():
    text = linux_build.dockerfile(DIGEST, "20260922T125549Z")
    lines = text.splitlines()
    assert lines[0] == f"FROM {DIGEST}"
    # Debian as it was when the image was made, security updates included...
    assert "https://snapshot.debian.org/archive/debian/20260922T125549Z/ trixie trixie-updates" in text
    assert "https://snapshot.debian.org/archive/debian-security/20260922T125549Z/ trixie-security" in text
    # ...and no installed package may change, so the -dev packages match the image's libraries exactly.
    hold = next(i for i, l in enumerate(lines) if "apt-mark hold" in l)
    install = next(i for i, l in enumerate(lines) if "apt-get install" in l)
    assert hold < install
    for dev in ("libwxgtk3.2-dev", "libprotobuf-dev", "libgit2-dev", "libocct-foundation-dev", "libngspice0-dev"):
        assert dev in text
    # KiCad's own cmake options for the official image (kicad-docker Dockerfile.10.0-stable).
    for flag in ("-DKICAD_SCRIPTING_WXPYTHON=ON", "-DKICAD_USE_OCC=ON", "-DKICAD_SPICE=ON",
                 "-DKICAD_USE_CMAKE_FINDPROTOBUF=ON", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_INSTALL_PREFIX=/usr"):
        assert flag in text
    assert "ninja kicad-cli eeschema_kiface cvpcb_kiface pcbnew_kiface" in text
    assert "-name _cvpcb.kiface" in text and "-name _pcbnew.kiface" in text  # copied out with the rest


def _bundle(root: Path, libs: list[str]) -> None:
    for rel in ("libexec/kicad-cli", "libexec/_eeschema.kiface", "libexec/_cvpcb.kiface", "libexec/_pcbnew.kiface",
                *(f"lib/{l}" for l in libs), "lib/libwx.so.0"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("official")


def test_overlay_replaces_kicads_own_files_only(tmp_path):
    root, built = tmp_path / "bundle", tmp_path / "built"
    _bundle(root, ["libkicommon.so.10.0.6", "libkigal.so.10.0.6"])
    built.mkdir()
    for name in ("kicad-cli", "_eeschema.kiface", "_cvpcb.kiface", "_pcbnew.kiface", "libkicommon.so.10.0.6", "libkigal.so.10.0.6",
                 "libkiapi.so.10.0.6"):
        (built / name).write_text("simee")
    replaced = linux_build.overlay(root, built)
    assert sorted(replaced) == ["lib/libkicommon.so.10.0.6", "lib/libkigal.so.10.0.6",
                                "libexec/_cvpcb.kiface", "libexec/_eeschema.kiface", "libexec/_pcbnew.kiface",
                                "libexec/kicad-cli"]
    for rel in replaced:
        assert (root / rel).read_text() == "simee"
    assert (root / "lib/libwx.so.0").read_text() == "official"
    assert not (root / "lib/libkiapi.so.10.0.6").exists()  # not in the official closure, so not needed


def test_overlay_refuses_to_leave_an_official_kicad_file(tmp_path):
    root, built = tmp_path / "bundle", tmp_path / "built"
    _bundle(root, ["libkicommon.so.10.0.6", "libkigal.so.10.0.6"])
    built.mkdir()
    for name in ("kicad-cli", "_eeschema.kiface", "_cvpcb.kiface", "_pcbnew.kiface", "libkicommon.so.10.0.6"):
        (built / name).write_text("simee")
    with pytest.raises(RuntimeError, match="libkigal.so.10.0.6"):
        linux_build.overlay(root, built)


def test_overlay_of_another_version_swaps_kicads_libraries_for_its_own(tmp_path):
    # a release-candidate rehearsal builds 10.0.7 on the 10.0.6 image: KiCad's libraries carry their version
    root, built = tmp_path / "bundle", tmp_path / "built"
    _bundle(root, ["libkicommon.so.10.0.6", "libkigal.so.10.0.6"])
    built.mkdir()
    for name in ("kicad-cli", "_eeschema.kiface", "_cvpcb.kiface", "_pcbnew.kiface", "libkicommon.so.10.0.7", "libkigal.so.10.0.7",
                 "libkiapi.so.10.0.7"):
        (built / name).write_text("simee")
    replaced = linux_build.overlay(root, built)
    assert sorted(replaced) == ["lib/libkicommon.so.10.0.7", "lib/libkigal.so.10.0.7",
                                "libexec/_cvpcb.kiface", "libexec/_eeschema.kiface", "libexec/_pcbnew.kiface",
                                "libexec/kicad-cli"]
    assert sorted(p.name for p in (root / "lib").glob("libki*")) == ["libkicommon.so.10.0.7", "libkigal.so.10.0.7"]


def test_source_versions_must_match_the_image():
    image = {"libwxgtk3.2-1t64": ("wxwidgets3.2", "3.2.8+dfsg-2"), "libgit2-1.9": ("libgit2", "1.9.0+ds-2")}
    same = {"libwxgtk3.2-dev": ("wxwidgets3.2", "3.2.8+dfsg-2"), "cmake": ("cmake", "3.31.6-2")}
    assert linux_build.mismatched_sources(image, same) == []
    newer = {"libgit2-dev": ("libgit2", "1.9.0+ds-2+deb13u1")}
    assert linux_build.mismatched_sources(image, newer) == [("libgit2", "1.9.0+ds-2", "1.9.0+ds-2+deb13u1")]


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_cli_passes_simee_ref_to_the_packager(monkeypatch, tmp_path, platform):
    from kicad_bundle import cli
    seen = {}
    monkeypatch.setitem(cli.PACKAGERS, platform, lambda *a, **k: seen.update(k) or [])
    cli.main(["--kicad-version", "10.0.6", "--platform", platform, "--simee-ref", "simee/10.0.6", "--cache", str(tmp_path)])
    assert seen == {"simee_ref": "simee/10.0.6"}


def test_cli_passes_a_base_image_to_the_linux_packager(monkeypatch, tmp_path):
    from kicad_bundle import cli
    seen = {}
    monkeypatch.setitem(cli.PACKAGERS, "linux", lambda *a, **k: seen.update(k) or [])
    cli.main(["--kicad-version", "11.0.0-rc1", "--platform", "linux", "--simee-ref", "rehearsal/11.0.0-rc1",
              "--base-image", "kicad/kicad:10.0.6", "--cache", str(tmp_path)])
    assert seen == {"simee_ref": "rehearsal/11.0.0-rc1", "base_image": "kicad/kicad:10.0.6"}


@pytest.mark.parametrize("platform", ["macos", "windows"])
def test_cli_refuses_a_base_image_outside_linux(monkeypatch, tmp_path, platform):
    from kicad_bundle import cli
    monkeypatch.setitem(cli.PACKAGERS, platform, lambda *a, **k: pytest.fail("packaged"))
    with pytest.raises(SystemExit):
        cli.main(["--kicad-version", "11.0.0-rc1", "--platform", platform, "--simee-ref", "x",
                  "--base-image", "kicad/kicad:10.0.6", "--cache", str(tmp_path)])
