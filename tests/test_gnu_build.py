import stat
from pathlib import Path

import pytest
import tiny_macho
from kicad_bundle import gnu_build

CONFIGURE = """#!/bin/sh
echo "$@" > configured
printf 'all:\\n\\techo all $(DOCS) >> made\\none:\\n\\techo one $(DOCS) >> made\\n' > Makefile
printf 'install:\\n\\techo install $(DOCS) >> made\\n' >> Makefile
"""


def _source(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "configure").write_text(CONFIGURE)
    (src / "configure").chmod(stat.S_IRWXU)
    return src


def test_make_configures_makes_and_installs_in_its_own_folder(tmp_path):
    gnu_build.make(_source(tmp_path), tmp_path / "build", ["--prefix=/p"], {"PATH": "/usr/bin:/bin"}, 2)
    assert (tmp_path / "build/configured").read_text().split() == ["--prefix=/p"]
    assert (tmp_path / "build/made").read_text().split("\n")[:2] == ["all", "install"]


def test_make_vars_reach_every_make_and_targets_replace_all(tmp_path):
    gnu_build.make(_source(tmp_path), tmp_path / "build", [], {"PATH": "/usr/bin:/bin"}, 2, targets=("one",),
                   make_vars=("DOCS=true",))
    assert (tmp_path / "build/made").read_text().split("\n")[:2] == ["one true", "install true"]


def test_cross_building_names_both_machines():
    assert gnu_build.host_args("x86_64-apple-darwin", "aarch64-apple-darwin25") == [
        "--build=aarch64-apple-darwin25", "--host=x86_64-apple-darwin"]
    assert gnu_build.host_args("x86_64-apple-darwin", None) == []


def test_check_macos_min_refuses_a_program_for_a_newer_macos_than_the_toolchains_minimum(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "avr-gcc").write_bytes(tiny_macho.thin("x86_64", "11111111-1111-1111-1111-111111111111", (11, 0)))
    (tmp_path / "bin" / "avr-cc").symlink_to("avr-gcc")
    gnu_build.check_macos_min(tmp_path)
    (tmp_path / "bin" / "avr-as").write_bytes(tiny_macho.thin("x86_64", "22222222-2222-2222-2222-222222222222", (15, 0)))
    with pytest.raises(RuntimeError, match="avr-as"):
        gnu_build.check_macos_min(tmp_path)
