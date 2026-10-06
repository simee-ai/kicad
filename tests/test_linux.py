import io
import os
import subprocess
import tarfile
from pathlib import Path

from kicad_bundle import linux
from tiny_elf import make_elf


def _tar(entries: dict[str, str | None], links: dict[str, str]) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, text in entries.items():
            info = tarfile.TarInfo(name)
            if text is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(text)
                tar.addfile(info, io.BytesIO(text.encode()))
        for name, target in links.items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    buf.seek(0)
    return buf


def test_extract_keeps_only_what_the_closure_can_need(tmp_path):
    stream = _tar({"usr/bin/kicad-cli": "cli", "usr/bin/kicad": "gui", "usr/lib/x86_64-linux-gnu/libfoo.so.1": "foo",
                   "usr/share/kicad/schemas/api.v1.schema.json": "{}", "usr/share/kicad/symbols/Device.kicad_sym": "",
                   "usr/share/doc/README": "", "etc/passwd": ""},
                  {"lib": "usr/lib", "lib64": "usr/lib64", "etc/alternatives/x": "/usr/bin/kicad"})
    linux.extract(stream, tmp_path)
    found = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if not p.is_dir() or p.is_symlink())
    assert found == ["etc/alternatives/x", "lib", "lib64", "usr/bin/kicad-cli",
                     "usr/lib/x86_64-linux-gnu/libfoo.so.1", "usr/share/kicad/schemas/api.v1.schema.json"]


def _image(rootfs):
    """A fake extracted image: kicad-cli -> libkicommon -> libc, plus the kiface and schemas."""
    libdir = rootfs / "usr/lib/x86_64-linux-gnu"
    make_elf(libdir / "libkicommon.so.10.0.6", needed=("libc.so.6",), soname="libkicommon.so.10.0.6")
    make_elf(libdir / "libgit2.so.1.9.0", soname="libgit2.so.1.9")
    os.symlink("libgit2.so.1.9.0", libdir / "libgit2.so.1.9")
    make_elf(rootfs / "usr/bin/kicad-cli", needed=("libkicommon.so.10.0.6", "libc.so.6")).chmod(0o755)
    make_elf(rootfs / "usr/bin/_eeschema.kiface", needed=("libgit2.so.1.9",))
    (rootfs / "usr/share/kicad/schemas").mkdir(parents=True)
    (rootfs / "usr/share/kicad/schemas/api.v1.schema.json").write_text("{}")
    os.symlink("usr/lib", rootfs / "lib")


def test_assemble_lays_out_a_relocatable_bundle(tmp_path):
    rootfs, root = tmp_path / "rootfs", tmp_path / "kicad-cli-10.0.6-linux-x86_64"
    _image(rootfs)
    linux.assemble(rootfs, root)
    found = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert found == ["bin/kicad-cli", "lib/libgit2.so.1.9", "lib/libkicommon.so.10.0.6", "libexec/_eeschema.kiface",
                     "libexec/kicad-cli", "share/kicad/schemas/api.v1.schema.json"]
    assert os.access(root / "bin/kicad-cli", os.X_OK) and os.access(root / "libexec/kicad-cli", os.X_OK)


def test_wrapper_points_kicad_cli_at_the_bundle_even_through_a_symlink(tmp_path):
    root = tmp_path / "bundle"
    (root / "bin").mkdir(parents=True)
    (root / "libexec").mkdir()
    linux.write_wrapper(root / "bin/kicad-cli")
    fake = root / "libexec/kicad-cli"
    fake.write_text('#!/bin/sh\necho "$LD_LIBRARY_PATH|$KICAD_STOCK_DATA_HOME|$*"\n')
    fake.chmod(0o755)
    os.symlink(root / "bin/kicad-cli", tmp_path / "kc")
    env = {"PATH": os.environ["PATH"], "LD_LIBRARY_PATH": "/opt/x"}
    real = root.resolve()
    out = subprocess.run([tmp_path / "kc", "sch", "export"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == f"{real}/lib:/opt/x|{real}/share/kicad|sch export"
    env = {"PATH": os.environ["PATH"], "KICAD_STOCK_DATA_HOME": "/data"}
    out = subprocess.run([root / "bin/kicad-cli"], env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == f"{real}/lib|/data|"


def test_smoke_runs_in_a_bare_container_of_the_oldest_supported_host(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cmd = linux.smoke_command(Path("work/bundle"))  # docker mounts need absolute paths
    bundle = tmp_path.resolve() / "work/bundle"
    assert cmd[:2] == ["docker", "run"] and linux.SMOKE_IMAGE in cmd
    assert f"{bundle}:{bundle}:ro" in cmd
    assert cmd[-1] == str(bundle / "bin/kicad-cli")
