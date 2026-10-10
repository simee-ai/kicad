import os
import tarfile
import zipfile

from kicad_bundle.bundle import archive, prune


def test_prune_keeps_closure_and_symlinks_into_it(tmp_path):
    fw = tmp_path / "Frameworks"
    (fw / "Python.framework" / "Versions" / "3.9" / "lib").mkdir(parents=True)
    (fw / "Python.framework" / "Versions" / "3.9" / "Python").write_text("py")
    (fw / "Python.framework" / "Versions" / "3.9" / "lib" / "os.py").write_text("stdlib")
    os.symlink("3.9", fw / "Python.framework" / "Versions" / "Current")
    (fw / "libgit2.1.9.6.dylib").write_text("git")
    os.symlink("libgit2.1.9.6.dylib", fw / "libgit2.1.9.dylib")
    (fw / "libTKBool.dylib").write_text("occ")
    os.symlink("libTKBool.dylib", fw / "libTKBool.7.dylib")

    keep = {fw / "Python.framework/Versions/3.9/Python", fw / "libgit2.1.9.6.dylib"}
    prune([fw], keep)

    remaining = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert remaining == ["Frameworks", "Frameworks/Python.framework", "Frameworks/Python.framework/Versions",
                         "Frameworks/Python.framework/Versions/3.9", "Frameworks/Python.framework/Versions/3.9/Python",
                         "Frameworks/Python.framework/Versions/Current", "Frameworks/libgit2.1.9.6.dylib",
                         "Frameworks/libgit2.1.9.dylib"]


def test_archive_formats_preserve_layout(tmp_path):
    root = tmp_path / "kicad-cli-1.0-x"
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "kicad-cli").write_text("cli")
    for fmt in ("tar.gz", "tar.xz"):
        tar = archive(root, tmp_path / "out", fmt)
        assert tar.name == f"kicad-cli-1.0-x.{fmt}"
        with tarfile.open(tar) as t:
            assert "kicad-cli-1.0-x/bin/kicad-cli" in t.getnames()
    zf = archive(root, tmp_path / "out", "zip")
    with zipfile.ZipFile(zf) as z:
        assert "kicad-cli-1.0-x/bin/kicad-cli" in z.namelist()
