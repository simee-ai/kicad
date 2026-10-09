import subprocess
from pathlib import Path

import pytest

import tiny_macho
from kicad_bundle.brew_prefix import (default_prefix, keg_only, link, placeholder_changes, relocate_text,
                                      use_libraries, wrap_config_tool)
from kicad_bundle.bundle import remove

ROOT = Path("/b")


def test_relocate_text_points_homebrews_placeholders_at_the_private_prefix():
    pc = b"prefix=@@HOMEBREW_CELLAR@@/protobuf/35.1\nRequires: absl_base\nlibdir=@@HOMEBREW_PREFIX@@/opt/abseil/lib\n"
    assert relocate_text(pc, ROOT) == b"prefix=/b/Cellar/protobuf/35.1\nRequires: absl_base\nlibdir=/b/opt/abseil/lib\n"


def test_placeholder_changes_rewrites_only_placeholder_paths():
    names = ["@@HOMEBREW_PREFIX@@/opt/abseil/lib/libabsl_base.2601.0.0.dylib", "/usr/lib/libc++.1.dylib",
             "@rpath/libutf8_validity.35.1.0.dylib", "@@HOMEBREW_CELLAR@@/protobuf/35.1/lib/libprotobuf.35.1.0.dylib"]
    assert placeholder_changes(names, ROOT) == [
        ("@@HOMEBREW_PREFIX@@/opt/abseil/lib/libabsl_base.2601.0.0.dylib", "/b/opt/abseil/lib/libabsl_base.2601.0.0.dylib"),
        ("@@HOMEBREW_CELLAR@@/protobuf/35.1/lib/libprotobuf.35.1.0.dylib",
         "/b/Cellar/protobuf/35.1/lib/libprotobuf.35.1.0.dylib")]


def _keg(root: Path, formula: str, files: list[str]) -> Path:
    keg = root / "Cellar" / formula / "1.0"
    for rel in files:
        (keg / rel).parent.mkdir(parents=True, exist_ok=True)
        (keg / rel).write_text(formula)
    return keg


def test_link_merges_kegs_into_the_prefix_like_brew_link(tmp_path):
    boost = _keg(tmp_path, "boost", ["include/boost/version.hpp", "lib/libboost_locale.dylib",
                                     "lib/cmake/Boost-1.90.0/BoostConfig.cmake"])
    cairo = _keg(tmp_path, "cairo", ["include/cairo/cairo.h", "lib/libcairo.2.dylib", "lib/pkgconfig/cairo.pc"])
    pixman = _keg(tmp_path, "pixman", ["include/pixman-1/pixman.h", "lib/pkgconfig/pixman-1.pc"])
    (boost / "include/.DS_Store").write_text("finder")
    for keg in (boost, cairo, pixman):
        link(keg, tmp_path)
    assert not (tmp_path / "include/.DS_Store").exists()
    assert (tmp_path / "include/boost/version.hpp").read_text() == "boost"
    assert (tmp_path / "include/cairo/cairo.h").read_text() == "cairo"
    assert (tmp_path / "lib/pkgconfig/cairo.pc").read_text() == "cairo"
    assert (tmp_path / "lib/pkgconfig/pixman-1.pc").read_text() == "pixman"
    assert (tmp_path / "lib/cmake/Boost-1.90.0/BoostConfig.cmake").exists()
    assert not (boost / "lib/pkgconfig").exists()  # linking never writes into a keg


def test_a_keg_built_from_source_gets_the_official_libraries_in_its_stand_in_bottle(tmp_path):
    # x86_64 openssl@3 3.6.5: Homebrew built it from source, so the arm64 bottle of 3.6.5 is poured for
    # its headers and pkg-config files, and its libraries are swapped for the official app's own
    keg = tmp_path / "Cellar/openssl@3/3.6.5"
    (keg / "lib/pkgconfig").mkdir(parents=True)
    (keg / "lib/libssl.3.dylib").write_bytes(tiny_macho.thin("arm64", "11111111-0000-0000-0000-000000000000"))
    (keg / "lib/libssl.dylib").symlink_to("libssl.3.dylib")
    (keg / "lib/libssl.a").write_bytes(b"!<arch>\n")
    (keg / "lib/pkgconfig/libssl.pc").write_text("libdir=x")
    frameworks = tmp_path / "KiCad.app/Contents/Frameworks"
    frameworks.mkdir(parents=True)
    intel = tiny_macho.thin("x86_64", "22222222-0000-0000-0000-000000000000")
    (frameworks / "libssl.3.dylib").write_bytes(
        tiny_macho.fat(("x86_64", intel), ("arm64", tiny_macho.thin("arm64", "33333333-0000-0000-0000-000000000000"))))
    assert use_libraries(keg, frameworks, "x86_64") == ["libssl.3.dylib"]
    assert (keg / "lib/libssl.3.dylib").read_bytes() == intel
    assert (keg / "lib/libssl.dylib").is_symlink()
    assert (keg / "lib/libssl.a").read_bytes() == b"!<arch>\n"
    with pytest.raises(RuntimeError, match="none of .*openssl@3/3.6.5.* is in"):
        use_libraries(keg, tmp_path, "x86_64")


def test_keg_only_reads_the_formula():
    assert keg_only('class Icu4cAT78 < Formula\n  keg_only :versioned_formula\nend\n')
    assert keg_only('class Openssl < Formula\n  keg_only :provided_by_macos\n')
    assert not keg_only('class Boost < Formula\n  url "x"\nend\n')


def test_remove_deletes_read_only_kegs(tmp_path):

    keg = _keg(tmp_path, "glm", ["include/glm/glm.hpp"])
    (keg / "include/glm/glm.hpp").chmod(0o444)
    (keg / "include/glm").chmod(0o555)
    remove(tmp_path / "Cellar")
    assert not (tmp_path / "Cellar").exists()


def test_default_prefix_is_where_homebrew_built_the_bottle():
    assert default_prefix("arm64_sonoma") == "/opt/homebrew"
    assert default_prefix("sonoma") == "/usr/local"


def test_config_tools_compiled_with_homebrews_prefix_are_wrapped_to_print_ours(tmp_path):
    tool = tmp_path / "odbc_config"
    tool.write_text("#!/bin/sh\necho -L/usr/local/Cellar/unixodbc/2.3.14/lib -lodbc -I/usr/local/opt/x/include\nexit 3\n")
    tool.chmod(0o755)
    wrap_config_tool(tool, "/usr/local", Path("/p"))
    result = subprocess.run([str(tool), "--libs"], capture_output=True, text=True)
    assert result.stdout == "-L/p/Cellar/unixodbc/2.3.14/lib -lodbc -I/p/opt/x/include\n"
    assert result.returncode == 3
