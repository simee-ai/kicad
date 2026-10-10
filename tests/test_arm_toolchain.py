import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from arm_toolchain import check, components, package, publish
from arm_toolchain.components import BINARIES, RELEASE

from archives import targz, zip_

ARM_TOP = "arm-gnu-toolchain-15.2.rel1-darwin-arm64-arm-none-eabi"
# A few files of each kind in Arm's release, and whether an RP2040 build needs them.
ARM_FILES = {
    "bin/arm-none-eabi-gcc": True, "bin/arm-none-eabi-gcc-15.2.1": True, "bin/arm-none-eabi-g++": True,
    "bin/arm-none-eabi-c++": False, "bin/arm-none-eabi-as": True, "bin/arm-none-eabi-ld.bfd": True,
    "bin/arm-none-eabi-objcopy": True, "bin/arm-none-eabi-size": True, "bin/arm-none-eabi-gdb": False,
    "bin/arm-none-eabi-gfortran": False, "bin/arm-none-eabi-gcov": False, "bin/arm-none-eabi-gcc.exe": True,
    "libexec/gcc/arm-none-eabi/15.2.1/cc1": True, "libexec/gcc/arm-none-eabi/15.2.1/cc1plus": True,
    "libexec/gcc/arm-none-eabi/15.2.1/lto1": True, "libexec/gcc/arm-none-eabi/15.2.1/lto-wrapper": True,
    "libexec/gcc/arm-none-eabi/15.2.1/collect2": True, "libexec/gcc/arm-none-eabi/15.2.1/f951": False,
    "libexec/gcc/arm-none-eabi/15.2.1/liblto_plugin.so": True,
    "arm-none-eabi/bin/as": True, "arm-none-eabi/include/stdio.h": True,
    "arm-none-eabi/include/c++/15.2.1/vector": True,
    "arm-none-eabi/lib/thumb/v6-m/nofp/libc.a": True, "arm-none-eabi/lib/thumb/v6-m/nofp/nosys.specs": True,
    "arm-none-eabi/lib/thumb/v7e-m+fp/hard/libc.a": False, "arm-none-eabi/lib/libc.a": False,
    "arm-none-eabi/lib/nosys.specs": True, "arm-none-eabi/lib/nano.specs": True,
    "lib/gcc/arm-none-eabi/15.2.1/include/stdint.h": True, "lib/gcc/arm-none-eabi/15.2.1/include-fixed/README": True,
    "lib/gcc/arm-none-eabi/15.2.1/thumb/v6-m/nofp/libgcc.a": True,
    "lib/gcc/arm-none-eabi/15.2.1/thumb/v7-m/nofp/libgcc.a": False,
    "lib/gcc/arm-none-eabi/15.2.1/libgcc.a": False, "lib/gcc/arm-none-eabi/15.2.1/plugin/include/tree.h": False,
    "share/doc/gcc/index.html": False, "share/gdb/python/gdb/__init__.py": False,
    "15.2.rel1-darwin-arm64-arm-none-eabi-manifest.txt": True, "manifest.txt": True, ".version": False,
}


def test_every_arm_download_is_pinned_by_sha256():
    for b in [*BINARIES.values(), components.SOURCE]:
        assert len(b.sha256) == 64 and b.url.startswith(f"https://developer.arm.com/-/media/Files/downloads/gnu/{RELEASE}/")
        assert b.url.endswith("/" + b.archive)
    assert components.SOURCE.archive == f"arm-gnu-toolchain-src-snapshot-{RELEASE}.tar.xz"


def test_the_hosts_arm_builds_for_and_their_archives():
    """Arm builds no macOS x86_64 toolchain after 14.2.rel1 (README)."""
    assert set(BINARIES) == {"macos-arm64", "linux-x86_64", "linux-arm64", "windows-x86_64"}
    assert BINARIES["macos-arm64"].archive == f"arm-gnu-toolchain-{RELEASE}-darwin-arm64-arm-none-eabi.tar.xz"
    assert BINARIES["windows-x86_64"].archive == f"arm-gnu-toolchain-{RELEASE}-mingw-w64-x86_64-arm-none-eabi.zip"
    assert BINARIES["windows-x86_64"].format == "zip"
    assert all(b.format == "tar.xz" for h, b in BINARIES.items() if h != "windows-x86_64")


def test_release_assets_are_named_after_arms_release_and_the_host():
    assert components.name("linux-arm64") == f"arm-gcc-{RELEASE}-linux-arm64"


@pytest.mark.parametrize("rel,wanted", ARM_FILES.items())
def test_only_what_an_rp2040_build_runs_and_links_is_kept(rel, wanted):
    assert package.kept(rel) == wanted


def test_each_multilib_keeps_its_newlib_and_libgcc():
    assert package.kept("arm-none-eabi/lib/thumb/v6-m/nofp/libstdc++.a", ("thumb/v8-m.main+fp/softfp",)) is False
    assert package.kept("arm-none-eabi/lib/thumb/v8-m.main+fp/softfp/libc.a", ("thumb/v8-m.main+fp/softfp",))
    assert package.kept("lib/gcc/arm-none-eabi/15.2.1/thumb/v8-m.main+fp/softfp/crti.o", ("thumb/v8-m.main+fp/softfp",))


def _arm_archive(tmp_path: Path, fmt: str) -> Path:
    """Like Arm's: its tars hold one top folder, its Windows zip the toolchain root's contents."""
    if fmt == "zip":
        return zip_(tmp_path / "arm.zip", {rel: rel.encode() for rel in ARM_FILES})
    return targz(tmp_path / "arm.tar.gz", {f"{ARM_TOP}/{rel}": rel.encode() for rel in ARM_FILES})


@pytest.mark.parametrize("fmt", ["tar.gz", "zip"])
def test_extract_unpacks_only_the_kept_files_into_a_root_named_after_the_asset(tmp_path, fmt):
    root = package.extract(_arm_archive(tmp_path, fmt), tmp_path / "work", "arm-gcc-x")
    assert root == tmp_path / "work" / "arm-gcc-x"
    got = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert got == sorted(rel for rel, wanted in ARM_FILES.items() if wanted)
    assert (root / "bin/arm-none-eabi-gcc").read_text() == "bin/arm-none-eabi-gcc"


def test_extract_keeps_hard_links_within_the_kept_files(tmp_path):
    arm = tmp_path / "arm.tar.gz"
    with tarfile.open(arm, "w:gz") as tar:
        lib = f"{ARM_TOP}/arm-none-eabi/lib/thumb/v6-m/nofp"
        for name in ("libg.a", "libc.a"):
            info = tarfile.TarInfo(f"{lib}/{name}")
            if name == "libc.a":
                info.type, info.linkname = tarfile.LNKTYPE, f"{lib}/libg.a"
                tar.addfile(info)
            else:
                info.size = 3
                tar.addfile(info, io.BytesIO(b"lib"))
    root = package.extract(arm, tmp_path / "work", "arm-gcc-x")
    nofp = root / "arm-none-eabi/lib/thumb/v6-m/nofp"
    assert (nofp / "libc.a").read_bytes() == b"lib"
    assert (nofp / "libc.a").stat().st_ino == (nofp / "libg.a").stat().st_ino


def _snapshot(tmp_path: Path) -> Path:
    files = {f"./{c}/COPYING": c.encode() for c in (*components.SHIPPED, "glibc", "linux", "binutils-gdb--gdb")}
    files["./gcc/COPYING.RUNTIME"] = b"exception"
    files["./gcc/libstdc++-v3/COPYING"] = b"too deep"
    files["./gcc/gcc/cp/parser.cc"] = b""
    return targz(tmp_path / "snapshot.tar.gz", files)


def test_licences_of_every_shipped_component_come_from_the_source_snapshot(tmp_path):
    package.write_licences(_snapshot(tmp_path), tmp_path / "root")
    doc = tmp_path / "root/share/doc"
    assert sorted(p.name for p in doc.iterdir()) == sorted(components.SHIPPED)
    assert (doc / "gcc/COPYING.RUNTIME").read_text() == "exception"
    assert (doc / "newlib-cygwin/COPYING").read_text() == "newlib-cygwin"
    assert not (doc / "gcc/libstdc++-v3").exists()


def test_a_shipped_component_without_licences_fails(tmp_path):
    snapshot = targz(tmp_path / "s.tar.gz", {"./gcc/COPYING": b"gcc"})
    with pytest.raises(RuntimeError, match="binutils-gdb"):
        package.write_licences(snapshot, tmp_path / "root")


def test_notice_names_arms_download_the_trim_and_the_source_asset():
    text = package.notice("windows-x86_64")
    b = BINARIES["windows-x86_64"]
    assert b.url in text and b.sha256 in text and RELEASE in text
    assert "thumb/v6-m/nofp" in text and components.SOURCE.archive in text
    for licence in ("GPL-3.0-or-later", "Runtime Library Exception", "LGPL-3.0-or-later", "COPYING.NEWLIB"):
        assert licence in text
    assert "MinGW-w64" in text and "MinGW-w64" not in package.notice("linux-x86_64")


def test_package_writes_the_notice_and_archives_the_root(tmp_path):
    root = package.extract(_arm_archive(tmp_path, "tar.gz"), tmp_path / "work", components.name("linux-arm64"))
    out = package.package("linux-arm64", root, _snapshot(tmp_path), tmp_path / "dist")
    assert out.name == f"{components.name('linux-arm64')}.tar.xz"
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
    top = components.name("linux-arm64")
    assert {f"{top}/THIRD-PARTY.txt", f"{top}/share/doc/gcc/COPYING", f"{top}/bin/arm-none-eabi-gcc"} <= names


def test_elf_check_wants_an_arm_executable(tmp_path):
    elf = tmp_path / "a.elf"
    elf.write_bytes(b"\x7fELF\x01\x01\x01" + b"\0" * 9 + (2).to_bytes(2, "little") + (40).to_bytes(2, "little"))
    check.assert_arm_elf(elf)
    elf.write_bytes(b"\x7fELF\x01\x01\x01" + b"\0" * 9 + (2).to_bytes(2, "little") + (83).to_bytes(2, "little"))
    with pytest.raises(RuntimeError, match="not an Arm"):
        check.assert_arm_elf(elf)


def test_release_tag_counts_builds_of_one_arm_release_apart_from_the_others():
    tags = ["cli-10.0.6-1", "avr-gcc-15.3.0-1", "arm-gcc-15.2.rel1-1", "arm-gcc-14.2.rel1-3"]
    assert publish.next_tag(RELEASE, tags) == "arm-gcc-15.2.rel1-2"
    assert publish.next_tag(RELEASE, ["avr-gcc-15.2.rel1-4"]) == "arm-gcc-15.2.rel1-1"


def test_release_notes_list_the_checksums_to_pin_and_the_sources():
    sums = "aa  arm-gcc-15.2.rel1-linux-x86_64.tar.xz\nbb  arm-gnu-toolchain-src-snapshot-15.2.rel1.tar.xz\n"
    text = publish.release_notes("https://run/1", sums)
    assert sums.strip() in text and "https://run/1" in text
    assert components.SOURCE.archive in text and "thumb/v6-m/nofp" in text and "macOS x86_64" in text
    assert "bin/arm-none-eabi-gcc" in text


# The real toolchain, when one is at hand: ARM_TOOLCHAIN=<archive> uv run pytest tests/test_arm_toolchain.py
@pytest.mark.skipif(not os.environ.get("ARM_TOOLCHAIN"), reason="ARM_TOOLCHAIN (a packaged archive) not set")
def test_a_packaged_toolchain_compiles_for_the_rp2040_from_a_copy_with_an_empty_path():
    check.check(Path(os.environ["ARM_TOOLCHAIN"]))
