import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from avr_toolchain import build, check, components, publish
from avr_toolchain.build import HOSTS
from kicad_bundle.debian import Package

from archives import targz

PREFIX = Path("/w/prefix")


def test_every_component_is_pinned_by_sha256_and_named_after_its_folder():
    for c in components.SOURCES:
        assert len(c.sha256) == 64 and c.url.startswith("https://")
        assert c.archive.startswith(c.folder + ".tar.")
    assert {c.name for c in components.SOURCES} == {"binutils", "gcc", "gmp", "mpfr", "mpc", "avr-libc"}


def test_release_assets_are_named_after_gcc_and_the_host():
    assert components.VERSION == components.GCC.version
    assert components.name("macos-arm64") == f"avr-gcc-{components.VERSION}-macos-arm64"
    assert components.SOURCE_ARCHIVE == f"avr-gcc-{components.VERSION}-source.tar"


def test_the_four_hosts_kicad_cli_ships_for():
    assert set(HOSTS) == {"macos-arm64", "macos-x86_64", "linux-x86_64", "windows-x86_64"}
    assert HOSTS["windows-x86_64"].exe == ".exe" and HOSTS["windows-x86_64"].archive == "zip"
    assert all(h.archive == "tar.gz" for n, h in HOSTS.items() if n != "windows-x86_64")


def test_a_host_is_native_only_on_its_own_os_and_cpu():
    assert HOSTS["macos-arm64"].native("darwin", "arm64")
    assert not HOSTS["macos-x86_64"].native("darwin", "arm64")
    assert HOSTS["linux-x86_64"].native("linux", "x86_64")
    assert not HOSTS["windows-x86_64"].native("linux", "x86_64")  # always cross-built, from Linux


def test_a_native_build_names_no_host_and_a_cross_build_names_both_machines():
    native = build.binutils_args(HOSTS["linux-x86_64"], PREFIX, cross_from=None)
    assert not any(a.startswith(("--host=", "--build=")) for a in native)
    cross = build.gcc_args(HOSTS["windows-x86_64"], PREFIX, cross_from="x86_64-pc-linux-gnu")
    assert "--host=x86_64-w64-mingw32" in cross and "--build=x86_64-pc-linux-gnu" in cross


def test_gcc_finds_its_assembler_and_linker_relative_to_itself():
    """--with-as/--with-ld would compile in absolute paths: the toolchain must run from any folder."""
    args = build.gcc_args(HOSTS["macos-arm64"], PREFIX, cross_from=None)
    assert not any(a.startswith(("--with-as", "--with-ld")) for a in args)
    assert "--target=avr" in args and f"--prefix={PREFIX}" in args
    assert "--enable-languages=c,c++" in args and "--with-avrlibc" in args
    for dependency in ("--without-zstd", "--without-isl"):  # nothing the user's machine may lack
        assert dependency in args


def test_binutils_builds_only_the_avr_tools():
    args = build.binutils_args(HOSTS["macos-arm64"], PREFIX, cross_from=None)
    assert "--target=avr" in args and "--disable-gdb" in args and "--disable-sim" in args
    assert "--without-zstd" in args


def test_macos_builds_use_the_os_zlib():
    for host in ("macos-arm64", "macos-x86_64"):
        assert "--with-system-zlib" in build.gcc_args(HOSTS[host], PREFIX, cross_from=None)
    assert "--with-system-zlib" not in build.gcc_args(HOSTS["linux-x86_64"], PREFIX, cross_from=None)


def test_avr_libc_is_built_for_the_avr_with_the_new_compiler():
    assert build.avr_libc_args(PREFIX) == ["--host=avr", f"--prefix={PREFIX}"]


def test_linux_and_windows_link_their_c_and_cxx_runtimes_statically():
    assert "-static-libstdc++" in HOSTS["linux-x86_64"].ldflags
    assert "-static" in HOSTS["windows-x86_64"].ldflags.split()
    assert HOSTS["macos-arm64"].ldflags == ""  # macOS's libc++ and libSystem are the OS's own


def test_build_env_uses_the_cross_compilers_only_when_cross_building(monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin")
    tools = Path("/tools")
    win = build.build_env(HOSTS["windows-x86_64"], native=False, target_tools=tools)
    assert win["CC"] == "x86_64-w64-mingw32-gcc" and win["LDFLAGS"] == HOSTS["windows-x86_64"].ldflags
    assert win["PATH"].split(os.pathsep)[0] == str(tools)
    mac = build.build_env(HOSTS["macos-arm64"], native=True, target_tools=tools)
    assert "CC" not in mac or mac["CC"] == os.environ.get("CC")
    assert mac["MACOSX_DEPLOYMENT_TARGET"] == build.MACOS_MIN


def test_target_libraries_lose_their_debug_info_and_nothing_else(tmp_path):
    """libgcc and avr-libc are built with -g, which triples the toolchain's size; firmware keeps its own."""
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "stripped"
    (tools / "avr-strip").write_text(f'#!/bin/sh\nfor f in "$@"; do echo "$f" >> {log}; done\n')
    (tools / "avr-strip").chmod(0o755)
    root = tmp_path / "root"
    for rel in ("lib/gcc/avr/15.3.0/avr5/libgcc.a", "avr/lib/avr5/libc.a", "avr/lib/avr5/crtatmega328p.o",
                "libexec/gcc/avr/15.3.0/cc1", "avr/include/avr/io.h"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("")
    build.strip_target_libraries(root, tools)
    stripped = log.read_text().split()
    assert stripped[0] == "--strip-debug"
    assert sorted(Path(p).relative_to(root).as_posix() for p in stripped[1:]) == [
        "avr/lib/avr5/crtatmega328p.o", "avr/lib/avr5/libc.a", "lib/gcc/avr/15.3.0/avr5/libgcc.a"]


def test_runtime_sources_include_what_a_package_was_built_using():
    pkgs = {
        Path("/usr/lib/gcc/x86_64-w64-mingw32/12-win32/libstdc++.a"):
            Package("g++-mingw-w64-x86-64-win32", "12.2.0-14+25.2", "gcc-mingw-w64", "25.2",
                    built_using=(("gcc-12", "12.2.0-14"),)),
        Path("/usr/x86_64-w64-mingw32/lib/libmingw32.a"):
            Package("mingw-w64-x86-64-dev", "10.0.0-3", "mingw-w64", "10.0.0-3"),
    }
    assert build.runtime_sources(pkgs) == {("gcc-mingw-w64", "25.2"), ("gcc-12", "12.2.0-14"),
                                           ("mingw-w64", "10.0.0-3")}


def test_notice_lists_every_component_with_its_licence_and_the_source_asset():
    text = build.notice("linux-x86_64", {Path("/usr/lib/gcc/x86_64-linux-gnu/12/libstdc++.a"):
                                         Package("libstdc++-12-dev", "12.2.0-14", "gcc-12", "12.2.0-14")})
    for c in components.SOURCES:
        assert f"{c.name} {c.version}" in text and c.url in text
    assert "GPL-3.0" in text and "BSD-3-Clause" in text and "Runtime Library Exception" in text
    assert components.SOURCE_ARCHIVE in text and components.RUNTIME_SOURCES in text
    assert "libstdc++.a\tlibstdc++-12-dev 12.2.0-14\tgcc-12 12.2.0-14" in text
    assert components.RUNTIME_SOURCES not in build.notice("macos-arm64", {})


def test_source_archive_holds_every_upstream_archive_and_the_build_scripts(tmp_path):
    archives = {c.name: targz(tmp_path / c.archive, {f"{c.folder}/COPYING": b"licence"})
                for c in components.SOURCES}
    dest = components.source_archive(archives, tmp_path / "dist" / components.SOURCE_ARCHIVE)
    with tarfile.open(dest) as tar:
        names = set(tar.getnames())
    top = components.SOURCE_ARCHIVE.removesuffix(".tar")
    for c in components.SOURCES:
        assert f"{top}/{c.folder}/{c.archive}" in names
    for script in ("avr_toolchain/build.py", "kicad_bundle/fetch.py", "pyproject.toml", "uv.lock"):
        assert f"{top}/simee-build/{script}" in names


def test_licences_go_to_share_doc_per_component(tmp_path):
    archives = {c.name: targz(tmp_path / c.archive, {f"{c.folder}/COPYING3": c.name.encode(),
                                                     f"{c.folder}/src/x.c": b""})
                for c in components.SOURCES}
    components.write_licences(archives, tmp_path / "root")
    assert (tmp_path / "root/share/doc/gcc/COPYING3").read_text() == "gcc"
    assert (tmp_path / "root/share/doc/avr-libc/COPYING3").read_text() == "avr-libc"
    assert not (tmp_path / "root/share/doc/gcc/src").exists()


def _fake_toolchain(tmp_path: Path, fmt: str) -> Path:
    root = tmp_path / "avr-gcc-x"
    (root / "bin").mkdir(parents=True)
    (root / "bin/avr-gcc").write_text("#!/bin/sh\n")
    if fmt == "zip":
        with zipfile.ZipFile(tmp_path / "t.zip", "w") as z:
            z.write(root / "bin/avr-gcc", "avr-gcc-x/bin/avr-gcc.exe")
        return tmp_path / "t.zip"
    with tarfile.open(tmp_path / "t.tar.gz", "w:gz") as tar:
        tar.add(root, arcname=root.name)
    return tmp_path / "t.tar.gz"


@pytest.mark.parametrize("fmt", ["tar.gz", "zip"])
def test_unpack_returns_the_toolchain_root_of_an_archive(tmp_path, fmt):
    root = check.unpack(_fake_toolchain(tmp_path, fmt), tmp_path / "out")
    assert root.name == "avr-gcc-x" and (root / "bin").is_dir()


def test_elf_check_wants_an_avr_executable(tmp_path):
    elf = tmp_path / "a.elf"
    elf.write_bytes(b"\x7fELF\x01\x01\x01" + b"\0" * 9 + (2).to_bytes(2, "little") + (83).to_bytes(2, "little"))
    check.assert_avr_elf(elf)
    elf.write_bytes(b"\x7fELF\x01\x01\x01" + b"\0" * 9 + (2).to_bytes(2, "little") + (62).to_bytes(2, "little"))
    with pytest.raises(RuntimeError, match="not an AVR"):
        check.assert_avr_elf(elf)


def test_release_tag_counts_builds_of_one_gcc_version_apart_from_kicad_cli():
    assert publish.next_tag("15.3.0", ["cli-10.0.6-1", "avr-gcc-15.3.0-1", "avr-gcc-14.2.0-3"]) == "avr-gcc-15.3.0-2"
    assert publish.next_tag("15.3.0", ["cli-15.3.0-4"]) == "avr-gcc-15.3.0-1"


def test_release_notes_list_the_checksums_to_pin_and_the_sources():
    sums = "aa  avr-gcc-15.3.0-linux-x86_64.tar.gz\nbb  avr-gcc-15.3.0-source.tar\n"
    text = publish.release_notes("https://run/1", sums)
    assert sums.strip() in text and "https://run/1" in text
    for c in components.SOURCES:
        assert f"{c.name} {c.version}" in text
    assert components.SOURCE_ARCHIVE in text and components.RUNTIME_SOURCES in text
    assert "bin/avr-gcc" in text


# The real toolchain, when one is at hand: AVR_TOOLCHAIN=<archive> uv run pytest
@pytest.mark.skipif(not os.environ.get("AVR_TOOLCHAIN"), reason="AVR_TOOLCHAIN (a built archive) not set")
def test_a_built_toolchain_compiles_blink_from_a_copy_with_an_empty_path():
    check.check(Path(os.environ["AVR_TOOLCHAIN"]))
