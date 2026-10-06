import os

from kicad_bundle.elf import deps, make_resolver, resolve_in
from tiny_elf import make_elf


def test_deps_come_from_the_dynamic_section(tmp_path):
    lib = make_elf(tmp_path / "libfoo.so.1.2.3", needed=("libbar.so.2", "libc.so.6"), soname="libfoo.so.1")
    assert deps(lib) == ["libbar.so.2", "libc.so.6"]
    assert deps(make_elf(tmp_path / "plain")) == []


def test_resolve_in_follows_symlinks_as_if_root_were_slash(tmp_path):
    real = tmp_path / "usr/lib/x86_64-linux-gnu/libfoo.so.1.2.3"
    real.parent.mkdir(parents=True)
    real.write_text("foo")
    os.symlink("usr/lib", tmp_path / "lib")                                                   # merged /usr
    os.symlink("/usr/lib/x86_64-linux-gnu/libfoo.so.1.2.3", real.parent / "libfoo.so.1")      # absolute
    os.symlink("../../../usr/./lib/x86_64-linux-gnu/libfoo.so.1", real.parent / "libfoo.so")  # relative
    assert resolve_in(tmp_path, "/lib/x86_64-linux-gnu/libfoo.so.1") == real
    assert resolve_in(tmp_path, "lib/x86_64-linux-gnu/libfoo.so") == real
    assert resolve_in(tmp_path, "/usr/lib/missing.so") == tmp_path / "usr/lib/missing.so"


def test_resolver_finds_image_libraries_and_leaves_glibc_to_the_host(tmp_path):
    real = make_elf(tmp_path / "usr/lib/x86_64-linux-gnu/libwx_baseu-3.2.so.0.3.0")
    os.symlink("libwx_baseu-3.2.so.0.3.0", real.parent / "libwx_baseu-3.2.so.0")
    os.symlink("usr/lib", tmp_path / "lib")
    resolve = make_resolver(tmp_path)
    cli = tmp_path / "usr/bin/kicad-cli"
    assert resolve("libwx_baseu-3.2.so.0", cli) == real
    for name in ("libc.so.6", "libm.so.6", "ld-linux-x86-64.so.2", "libpthread.so.0"):
        assert resolve(name, cli) is None
    assert not resolve("libgone.so.1", cli).exists()  # closure() reports it as missing
