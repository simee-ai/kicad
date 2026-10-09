import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

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
                   "usr/share/doc/libfoo1/copyright": "MIT", "usr/share/man/man1/kicad.1": "",
                   "var/lib/dpkg/status": "", "var/lib/dpkg/info/libfoo1.list": "", "etc/passwd": ""},
                  {"lib": "usr/lib", "lib64": "usr/lib64", "etc/alternatives/x": "/usr/bin/kicad"})
    linux.extract(stream, tmp_path)
    found = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if not p.is_dir() or p.is_symlink())
    assert found == ["etc/alternatives/x", "lib", "lib64", "usr/bin/kicad-cli",
                     "usr/lib/x86_64-linux-gnu/libfoo.so.1", "usr/share/doc/libfoo1/copyright",
                     "usr/share/kicad/schemas/api.v1.schema.json", "var/lib/dpkg/info/libfoo1.list",
                     "var/lib/dpkg/status"]


def _image(rootfs, extra_lib: str | None = None):
    """A fake extracted image: kicad-cli -> libkicommon -> libc, the kiface -> libgit2 (from Debian's
    libgit2-1.9, whose copyright file is reached through a symlinked doc dir), plus the schemas."""
    libdir = rootfs / "usr/lib/x86_64-linux-gnu"
    info = rootfs / "var/lib/dpkg/info"
    info.mkdir(parents=True)
    (rootfs / "var/lib/dpkg/status").write_text(
        "Package: libgit2-1.9\nStatus: install ok installed\nSource: libgit2\nVersion: 1.9.0+ds-2+deb13u1\n")
    (info / "libgit2-1.9:amd64.list").write_text(f"/usr/lib/x86_64-linux-gnu/libgit2.so.1.9.0\n")
    (rootfs / "usr/share/doc/libgit2-common").mkdir(parents=True)
    (rootfs / "usr/share/doc/libgit2-common/copyright").write_text("GPL-2 with linking exception")
    os.symlink("libgit2-common", rootfs / "usr/share/doc/libgit2-1.9")
    make_elf(libdir / "libkicommon.so.10.0.6", needed=("libc.so.6",), soname="libkicommon.so.10.0.6")
    make_elf(libdir / "libgit2.so.1.9.0", soname="libgit2.so.1.9")
    os.symlink("libgit2.so.1.9.0", libdir / "libgit2.so.1.9")
    make_elf(rootfs / "usr/bin/kicad-cli", needed=("libkicommon.so.10.0.6", "libc.so.6")).chmod(0o755)
    make_elf(rootfs / "usr/bin/_eeschema.kiface", needed=("libgit2.so.1.9", *([extra_lib] if extra_lib else [])))
    make_elf(rootfs / "usr/bin/_cvpcb.kiface", needed=("libkicommon.so.10.0.6",))
    if extra_lib:
        make_elf(libdir / extra_lib)
    (rootfs / "usr/share/kicad/schemas").mkdir(parents=True)
    (rootfs / "usr/share/kicad/schemas/api.v1.schema.json").write_text("{}")
    os.symlink("usr/lib", rootfs / "lib")


def test_assemble_lays_out_a_relocatable_bundle(tmp_path):
    rootfs, root = tmp_path / "rootfs", tmp_path / "kicad-cli-10.0.6-linux-x86_64"
    _image(rootfs)
    sources = linux.assemble(rootfs, root, "10.0.6")
    found = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    assert found == ["THIRD-PARTY.txt", "bin/kicad-cli", "lib/libgit2.so.1.9", "lib/libkicommon.so.10.0.6",
                     "libexec/_cvpcb.kiface", "libexec/_eeschema.kiface", "libexec/kicad-cli",
                     "share/doc/libgit2-1.9/copyright",
                     "share/kicad/schemas/api.v1.schema.json"]
    assert os.access(root / "bin/kicad-cli", os.X_OK) and os.access(root / "libexec/kicad-cli", os.X_OK)
    assert sources == {("libgit2", "1.9.0+ds-2+deb13u1")}


def test_assemble_lists_every_shipped_library_with_its_licence_and_source(tmp_path):
    rootfs, root = tmp_path / "rootfs", tmp_path / "kicad-cli-10.0.6-linux-x86_64"
    _image(rootfs)
    linux.assemble(rootfs, root, "10.0.6")
    assert (root / "share/doc/libgit2-1.9/copyright").read_text() == "GPL-2 with linking exception"
    notice = (root / "THIRD-PARTY.txt").read_text()
    assert "lib/libgit2.so.1.9\tlibgit2-1.9 1.9.0+ds-2+deb13u1\tlibgit2 1.9.0+ds-2+deb13u1" in notice
    assert "kicad-cli-10.0.6-linux-x86_64-sources.tar" in notice
    assert "kicad-10.0.6-source.tar.gz" in notice  # KiCad's own files: kicad-cli, the kiface, libkicommon
    assert "simee-kicad" not in notice


def test_assemble_of_a_simee_build_names_the_commit_its_kicad_files_come_from(tmp_path):
    rootfs, root = tmp_path / "rootfs", tmp_path / "kicad-cli-10.0.6-linux-x86_64"
    _image(rootfs)
    linux.assemble(rootfs, root, "10.0.6", simee_sha="4e18395976" + "0" * 30)
    first, second = (root / "THIRD-PARTY.txt").read_text().splitlines()[1:3]
    assert "simee-kicad commit 4e18395976" in first + second
    assert max(len(first), len(second)) <= 100


def test_assemble_on_another_image_names_it(tmp_path):
    # a release-candidate rehearsal: KiCad 11.0.0-rc1 built on the 10.0.6 image, which has no rc tags
    rootfs, root = tmp_path / "rootfs", tmp_path / "kicad-cli-11.0.0-rc1-linux-x86_64"
    _image(rootfs)
    linux.assemble(rootfs, root, "11.0.0-rc1", simee_sha="4e18395976" + "0" * 30, image="kicad/kicad:10.0.6")
    notice = (root / "THIRD-PARTY.txt").read_text()
    assert notice.startswith("kicad-cli 11.0.0-rc1 for Linux x86_64, repackaged from the official kicad/kicad:10.0.6 ")
    assert "kicad/kicad:11.0.0-rc1" not in notice


class FakeLinuxBuild:
    """Stands in for the docker steps of linux.package; records the image it was asked for."""

    def __init__(self, monkeypatch, tmp_path):
        self.images = []
        monkeypatch.setattr(linux, "_export_image", lambda image, rootfs: self.images.append(image) or "digest")
        monkeypatch.setattr(linux, "assemble", lambda rootfs, root, version, sha=None, image=None: set())
        monkeypatch.setattr(linux.simee_source, "resolve_ref", lambda ref: "f" * 40)
        monkeypatch.setattr(linux.simee_source, "source_archive", lambda sha, dest: dest)
        monkeypatch.setattr(linux.linux_build, "build", lambda image, rootfs, src, work: tmp_path / "built")
        monkeypatch.setattr(linux.linux_build, "overlay", lambda root, built: [])
        monkeypatch.setattr(linux, "archive", lambda root, out, kind: out / f"{root.name}.tar.gz")
        monkeypatch.setattr(linux.debian, "sources_archive", lambda sources, dest, cache: dest)


def test_package_takes_the_versions_own_image(monkeypatch, tmp_path):
    fake = FakeLinuxBuild(monkeypatch, tmp_path)
    linux.package("10.0.6", tmp_path / "dist", tmp_path / "cache", tmp_path / "work", run_smoke=False)
    assert fake.images == ["kicad/kicad:10.0.6"]


def test_package_builds_a_release_candidate_on_a_base_image(monkeypatch, tmp_path):
    fake = FakeLinuxBuild(monkeypatch, tmp_path)
    built = linux.package("11.0.0-rc1", tmp_path / "dist", tmp_path / "cache", tmp_path / "work", run_smoke=False,
                          simee_ref="rehearsal/11.0.0-rc1", base_image="kicad/kicad:10.0.6")
    assert fake.images == ["kicad/kicad:10.0.6"]
    assert built[0].name == "kicad-cli-11.0.0-rc1-linux-x86_64.tar.gz"


def test_a_base_image_needs_a_simee_ref(monkeypatch, tmp_path):
    # without one the bundle would be the base image's own kicad-cli, labelled as another version
    fake = FakeLinuxBuild(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="simee-ref"):
        linux.package("11.0.0-rc1", tmp_path / "dist", tmp_path / "cache", tmp_path / "work", run_smoke=False,
                      base_image="kicad/kicad:10.0.6")
    assert fake.images == []


def test_assemble_refuses_a_library_no_debian_package_owns(tmp_path):
    rootfs = tmp_path / "rootfs"
    _image(rootfs, extra_lib="libmystery.so.1")
    with pytest.raises(RuntimeError, match="libmystery.so.1"):
        linux.assemble(rootfs, tmp_path / "kicad-cli-10.0.6-linux-x86_64", "10.0.6")


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
