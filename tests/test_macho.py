from pathlib import Path

import tiny_macho
from kicad_bundle.macho import Slice, make_resolver, parse_otool, slices

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
