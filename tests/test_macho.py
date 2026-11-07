from pathlib import Path

import pytest
import tiny_macho
from kicad_bundle.macho import Slice, check_minos, is_macho, make_resolver, parse_install_id, parse_otool, parse_rpaths, slices

OTOOL = """/x/KiCad.app/Contents/MacOS/kicad-cli:
\t/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa (compatibility version 1.0.0, current version 24.0.0)
\t@rpath/libkicommon.10.0.6.dylib (compatibility version 0.0.0, current version 0.0.0)
\t@rpath/Versions/3.9/Python (compatibility version 3.9.0, current version 3.9.0)
\t/usr/lib/libc++.1.dylib (compatibility version 1.0.0, current version 1700.0.0)
"""


def test_parse_otool_skips_the_header_line():
    assert parse_otool(OTOOL) == ["/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa",
                                  "@rpath/libkicommon.10.0.6.dylib", "@rpath/Versions/3.9/Python",
                                  "/usr/lib/libc++.1.dylib"]


def test_resolver_maps_bundle_paths_and_skips_system(tmp_path):
    contents = tmp_path / "KiCad.app" / "Contents"
    (contents / "Frameworks" / "Python.framework" / "Versions" / "3.9").mkdir(parents=True)
    (contents / "Frameworks" / "libkicommon.10.0.6.dylib").touch()
    (contents / "Frameworks" / "Python.framework" / "Versions" / "3.9" / "Python").touch()
    resolve = make_resolver(contents)
    cli = contents / "MacOS" / "kicad-cli"
    assert resolve("/usr/lib/libc++.1.dylib", cli) is None
    assert resolve("/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa", cli) is None
    assert resolve("@rpath/libkicommon.10.0.6.dylib", cli) == contents / "Frameworks" / "libkicommon.10.0.6.dylib"
    # KiCad links Python as @rpath/Versions/3.9/Python, found inside Python.framework
    assert resolve("@rpath/Versions/3.9/Python", cli) == contents / "Frameworks/Python.framework/Versions/3.9/Python"
    assert resolve("@executable_path/../Frameworks/libkicommon.10.0.6.dylib", cli) == \
        contents / "Frameworks" / "libkicommon.10.0.6.dylib"
    plugin = contents / "PlugIns" / "_eeschema.kiface"
    assert resolve("@loader_path/sim/libngspice.0.dylib", plugin) == contents / "PlugIns" / "sim" / "libngspice.0.dylib"


def test_slices_reads_each_architectures_uuid_and_minimum_macos():
    arm = tiny_macho.thin("arm64", "7ad804a6-c91b-3a8f-81db-2e0726d3d42e", (14, 0))
    intel = tiny_macho.thin("x86_64", "00000000-0000-0000-0000-000000000001", (11, 6))
    assert slices(tiny_macho.fat(("x86_64", intel), ("arm64", arm))) == {
        "arm64": Slice("7AD804A6-C91B-3A8F-81DB-2E0726D3D42E", (14, 0)),
        "x86_64": Slice("00000000-0000-0000-0000-000000000001", (11, 6))}
    assert slices(arm) == {"arm64": Slice("7AD804A6-C91B-3A8F-81DB-2E0726D3D42E", (14, 0))}
    assert slices(b"#!/bin/sh\n") == {}


def test_slices_name_the_homebrew_kegs_each_architecture_was_built_in():
    # a keg built from source keeps its Cellar path in compiled-in directories (OpenSSL's ENGINESDIR)
    arm = tiny_macho.thin("arm64", "7ad804a6-c91b-3a8f-81db-2e0726d3d42e") + \
        b"/opt/homebrew/Cellar/openssl@3/3.6.5/lib/engines-3\0"
    intel = tiny_macho.thin("x86_64", "00000000-0000-0000-0000-000000000001") + \
        b"/usr/local/Cellar/openssl@3/3.6.4_1/lib/ossl-modules\0/usr/local/Cellar/openssl@3/3.6.4_1/lib/engines-3\0"
    found = slices(tiny_macho.fat(("x86_64", intel), ("arm64", arm)))
    assert found["arm64"].kegs == frozenset({("openssl@3", "3.6.5")})
    assert found["x86_64"].kegs == frozenset({("openssl@3", "3.6.4_1")})
    assert slices(tiny_macho.thin("arm64", "7ad804a6-c91b-3a8f-81db-2e0726d3d42e"))["arm64"].kegs == frozenset()


def test_slices_read_a_librarys_own_version_and_the_libraries_it_links():
    # KiCad's relinked x86_64 libgit2 1.9.7_2 (simee-kicad#58): what it links tells the formula revision apart
    lib = tiny_macho.thin("x86_64", "00000000-0000-0000-0000-000000000001", (15, 0),
                          dylib=("@rpath/libgit2.1.9.dylib", "1.9.7"),
                          deps=("@rpath/libssl.4.dylib", "@executable_path/../Frameworks/libcrypto.4.dylib",
                                "/System/Library/Frameworks/Security.framework/Versions/A/Security",
                                "/usr/lib/libSystem.B.dylib"))
    found = slices(lib)["x86_64"]
    assert found.version == "1.9.7"
    assert found.dylibs == frozenset({"libssl.4.dylib", "libcrypto.4.dylib", "Security", "libSystem.B.dylib"})
    plain = slices(tiny_macho.thin("arm64", "7ad804a6-c91b-3a8f-81db-2e0726d3d42e"))["arm64"]
    assert (plain.version, plain.dylibs) == (None, frozenset())


OTOOL_L = """/x/kicad-cli:
Load command 12
          cmd LC_LOAD_DYLIB
      cmdsize 56
         name @rpath/libkicommon.10.0.6.dylib (offset 24)
Load command 40
          cmd LC_RPATH
      cmdsize 48
         path @executable_path/../Frameworks (offset 12)
Load command 41
          cmd LC_RPATH
      cmdsize 64
         path @executable_path/../Frameworks/Python.framework (offset 12)
"""


def test_parse_rpaths_reads_every_lc_rpath():
    assert parse_rpaths(OTOOL_L) == ["@executable_path/../Frameworks",
                                     "@executable_path/../Frameworks/Python.framework"]


def test_parse_install_id_is_none_for_an_executable():
    assert parse_install_id("/x/libnng.1.dylib:\n@rpath/libnng.1.dylib\n") == "@rpath/libnng.1.dylib"
    assert parse_install_id("/x/kicad-cli:\n") is None


def test_is_macho_tells_a_fat_binary_from_a_java_class_file(tmp_path):
    fat, java = tmp_path / "fat", tmp_path / "TestAWT.class"
    fat.write_bytes(tiny_macho.fat(("arm64", tiny_macho.thin("arm64", "7AD804A6-C91B-3A8F-81DB-2E0726D3D42E"))))
    java.write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\0" * 32)  # same magic, then the class file version
    assert is_macho(fat)
    assert not is_macho(java)


def test_check_minos_accepts_images_for_the_minimum_or_older(tmp_path):
    (tmp_path / "older").write_bytes(tiny_macho.thin("arm64", "11111111-1111-1111-1111-111111111111", (11, 0)))
    (tmp_path / "same").write_bytes(tiny_macho.fat(
        ("arm64", tiny_macho.thin("arm64", "22222222-2222-2222-2222-222222222222", (11, 6))),
        ("x86_64", tiny_macho.thin("x86_64", "33333333-3333-3333-3333-333333333333", (10, 15)))))
    (tmp_path / "notes.txt").write_text("not a Mach-O file")
    check_minos(tmp_path.iterdir(), (11, 6))


def test_check_minos_names_each_image_built_for_a_newer_macos(tmp_path):
    (tmp_path / "ok").write_bytes(tiny_macho.thin("arm64", "11111111-1111-1111-1111-111111111111", (11, 0)))
    (tmp_path / "cc1").write_bytes(tiny_macho.fat(
        ("arm64", tiny_macho.thin("arm64", "22222222-2222-2222-2222-222222222222", (11, 0))),
        ("x86_64", tiny_macho.thin("x86_64", "33333333-3333-3333-3333-333333333333", (15, 2)))))
    with pytest.raises(RuntimeError, match=r"(?s)macOS than 11\.0.*/cc1 \(x86_64\): 15\.2") as raised:
        check_minos(sorted(tmp_path.iterdir()), (11, 0))
    assert "/ok " not in str(raised.value) and "(arm64)" not in str(raised.value)
