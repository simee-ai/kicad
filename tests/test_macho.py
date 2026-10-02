from pathlib import Path

from kicad_bundle.macho import make_resolver, parse_otool

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
