"""The macOS x86_64 Arm toolchain, which Arm stopped building after 14.2.rel1: host programs built from
Arm's source snapshot, target files from Arm's macOS arm64 build (README, "Arm toolchain")."""

import os
import tarfile
from pathlib import Path

import pytest

from arm_toolchain import build, components, package
from arm_toolchain.components import BINARIES, RELEASE, SOURCE

from archives import targz

ROOT = Path("/w/arm-gcc")
CROSS = "aarch64-apple-darwin25.2.0"
# Two lines of Arm's manifest for its macOS arm64 build, as Arm writes them (tabs and all).
MANIFEST = (
    "binutils_configure=--enable-initfini-array --disable-nls --without-x --disable-gdbtk --without-tcl --without-tk "
    "\t\t\t  --enable-plugins \t\t\t  --disable-gdb \t\t\t  --without-gdb \t\t\t  --target=arm-none-eabi "
    "\t\t\t  --prefix=/ \t\t\t  --with-bugurl=\"https://gitlab.arm.com/tooling/gnu-devtools-for-arm/-/issues/\" "
    "\t\t\t  --with-sysroot=//arm-none-eabi \t\t\t   \t\t\t   --without-debuginfod\n"
    "gcc2_configure=--target=arm-none-eabi \t\t\t--prefix=/Volumes/data/jenkins/install "
    "\t\t\t--with-gmp=/Volumes/data/jenkins/host-tools \t\t\t--with-mpfr=/Volumes/data/jenkins/host-tools "
    "\t\t\t--with-mpc=/Volumes/data/jenkins/host-tools \t\t\t \t\t\t--with-isl=/Volumes/data/jenkins/host-tools "
    "\t\t\t--disable-shared \t\t\t--disable-nls \t\t\t--disable-threads \t\t\t--enable-checking=release "
    "\t\t\t--enable-languages=c,c++,fortran \t\t\t--with-newlib                         --with-gnu-as "
    "\t\t\t--with-headers=yes                         --with-gnu-ld "
    "\t\t\t--with-native-system-header-dir=/include "
    "                        --with-sysroot=/Volumes/data/jenkins/install/arm-none-eabi "
    "\t\t\t--with-bugurl=\"https://gitlab.arm.com/tooling/gnu-devtools-for-arm/-/issues/\" "
    "\t\t\t \t\t\t  --with-multilib-list=aprofile,rmprofile \t\t\t\n"
)


def test_arms_configure_options_come_from_its_manifest():
    opts = build.arm_options(MANIFEST, "gcc2")
    assert "--with-multilib-list=aprofile,rmprofile" in opts and "--enable-checking=release" in opts
    assert "--with-bugurl=https://gitlab.arm.com/tooling/gnu-devtools-for-arm/-/issues/" in opts
    assert all(o.startswith("--") for o in opts)
    assert build.arm_options(MANIFEST, "binutils")[0] == "--enable-initfini-array"


def test_a_step_missing_from_the_manifest_fails():
    with pytest.raises(RuntimeError, match="gcc1_configure"):
        build.arm_options(MANIFEST, "gcc1")


def _flags(args: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for a in args:
        flag, _, value = a.partition("=")
        found.setdefault(flag, []).append(value)
    return found


@pytest.mark.parametrize("step", ["binutils", "gcc2"])
def test_binutils_and_gcc_are_configured_as_arm_did_in_our_folders(step):
    make_args = build.binutils_args if step == "binutils" else build.gcc_args
    args = make_args(build.arm_options(MANIFEST, step), ROOT, CROSS)
    flags = _flags(args)
    for arm in build.arm_options(MANIFEST, step):  # each of Arm's options, but those naming its folders
        flag = arm.partition("=")[0]
        if flag not in build.REPLACED:
            assert arm in args
    assert not [a for a in args if "/Volumes/" in a or "gitlab.arm.com" in a]
    assert all(len(v) == 1 for v in flags.values()), "an option given twice"
    assert flags["--prefix"] == [str(ROOT)]
    # Under the prefix, so GCC and ld find it relative to themselves wherever the root is copied.
    assert flags["--with-sysroot"] == [f"{ROOT}/arm-none-eabi"]
    assert flags["--build"] == [CROSS] and flags["--host"] == ["x86_64-apple-darwin"]
    assert flags["--with-bugurl"] == [build.BUGURL]
    assert "--without-zstd" in args  # Arm's programs link nothing but the OS's libraries
    assert "--with-system-zlib" in args  # binutils' and GCC's bundled zlib don't compile against current SDKs


def test_gcc_builds_only_c_and_cxx_and_says_whose_build_it_is():
    flags = _flags(build.gcc_args(build.arm_options(MANIFEST, "gcc2"), ROOT, None))
    assert flags["--enable-languages"] == ["c,c++"]  # Arm's fortran (f951) isn't kept
    [pkgversion] = flags["--with-pkgversion"]
    assert "Arm GNU Toolchain" in pkgversion and RELEASE in pkgversion and "simee" in pkgversion
    assert "--build" not in flags and "--host" not in flags  # on an Intel Mac, a native build


@pytest.mark.parametrize("rel,host", [
    ("bin/arm-none-eabi-gcc", True), ("libexec/gcc/arm-none-eabi/15.2.1/cc1", True),
    ("arm-none-eabi/bin/as", True), ("arm-none-eabi/include/stdio.h", False),
    ("arm-none-eabi/lib/thumb/v6-m/nofp/libc.a", False), ("lib/gcc/arm-none-eabi/15.2.1/include/stdint.h", False),
    ("15.2.rel1-darwin-arm64-arm-none-eabi-manifest.txt", False), ("binutils/x", False),
])
def test_host_programs_are_what_runs_on_the_host(rel, host):
    assert build.host_program(rel) == host


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


ARM_ROOT = {  # Arm's macOS arm64 toolchain, trimmed (package.extract)
    "arm-none-eabi/include/stdio.h": b"arm", "lib/gcc/arm-none-eabi/15.2.1/include/stdint.h": b"arm",
    "bin/arm-none-eabi-gcc": b"arm64", "bin/arm-none-eabi-as": b"arm64", "arm-none-eabi/bin/as": b"arm64",
    "libexec/gcc/arm-none-eabi/15.2.1/cc1": b"arm64"}
PROGRAMS = ["bin/arm-none-eabi-gcc", "bin/arm-none-eabi-as", "arm-none-eabi/bin/as",
            "libexec/gcc/arm-none-eabi/15.2.1/cc1"]


def test_arms_host_programs_are_what_the_build_must_make():
    assert build.arm_programs(ARM_ROOT) == sorted(PROGRAMS)


def test_assemble_takes_arms_target_side_and_the_built_host_programs_arm_ships(tmp_path):
    arm = _tree(tmp_path / "arm", ARM_ROOT)
    built = _tree(tmp_path / "stage", {
        **{p: b"x86" for p in PROGRAMS}, "bin/arm-none-eabi-gcov": b"x86",
        "lib/gcc/arm-none-eabi/15.2.1/include/stdint.h": b"ours",  # installed by GCC: Arm's wins
        "share/man/man1/arm-none-eabi-gcc.1": b"man"})
    (built / "bin/arm-none-eabi-gcc").chmod(0o755)
    root = build.assemble(arm, built, tmp_path / "root")
    got = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert got == {"arm-none-eabi/include/stdio.h": b"arm", "lib/gcc/arm-none-eabi/15.2.1/include/stdint.h": b"arm",
                   **{p: b"x86" for p in PROGRAMS}}
    assert os.access(root / "bin/arm-none-eabi-gcc", os.X_OK)


def test_assemble_keeps_arms_hard_links(tmp_path):
    """newlib's libg.a is libc.a in Arm's archives: linked, not copied, it is archived once."""
    arm = _tree(tmp_path / "arm", {"arm-none-eabi/lib/thumb/v6-m/nofp/libc.a": b"libc"})
    nofp = "arm-none-eabi/lib/thumb/v6-m/nofp"
    os.link(arm / nofp / "libc.a", arm / nofp / "libg.a")
    root = build.assemble(arm, tmp_path / "stage", tmp_path / "root")
    assert (root / nofp / "libc.a").stat().st_ino == (root / nofp / "libg.a").stat().st_ino


def test_assemble_refuses_a_build_missing_one_of_arms_host_programs(tmp_path):
    arm = _tree(tmp_path / "arm", ARM_ROOT)
    built = _tree(tmp_path / "stage", {p: b"x86" for p in PROGRAMS if not p.endswith("cc1")})
    with pytest.raises(RuntimeError, match="cc1"):
        build.assemble(arm, built, tmp_path / "root")


def test_the_install_prefix_starts_as_arms_sysroot_and_nothing_else(tmp_path):
    arm = _tree(tmp_path / "arm", ARM_ROOT)
    stage = build.sysroot_copy(arm, tmp_path / "stage")
    got = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
    assert got == ["arm-none-eabi/include/stdio.h", "lib/gcc/arm-none-eabi/15.2.1/include/stdint.h"]
    (stage / "arm-none-eabi/include/stdio.h").write_bytes(b"installed over")  # a copy, not a link
    assert (arm / "arm-none-eabi/include/stdio.h").read_bytes() == b"arm"


def test_the_snapshot_unpacks_only_what_the_build_compiles_with_gccs_libraries_in_its_tree(tmp_path):
    snapshot = targz(tmp_path / "snap.tar.gz", {f"./{c}/configure": c.encode() for c in (
        *build.SNAPSHOT_FOLDERS, "glibc", "binutils-gdb--gdb")})
    src = build.unpack_snapshot(snapshot, tmp_path / "src")
    assert sorted(p.name for p in src.iterdir()) == sorted(build.SNAPSHOT_FOLDERS)
    for lib in ("gmp", "mpfr", "mpc", "isl"):
        assert (src / "gcc" / lib / "configure").read_text() == lib


def test_the_build_compiles_for_intel_macs_from_either_kind_of_mac():
    env = build.build_env({"PATH": "/usr/bin", "CC": "gcc"}, None)
    assert env["CC"] == "clang -arch x86_64" and env["CXX"] == "clang++ -arch x86_64"
    assert env["MACOSX_DEPLOYMENT_TARGET"] == "11.0" and env["PATH"] == "/usr/bin"


def test_a_cross_build_runs_arms_own_toolchain_for_this_mac():
    """GCC's cross build runs arm-none-eabi-gcc for this machine (-dumpspecs): Arm's arm64 build of it."""
    env = build.build_env({"PATH": "/usr/bin"}, Path("/w/arm"))
    assert env["PATH"] == f"/w/arm/bin{os.pathsep}/usr/bin"
    # Arm's snapshot is a git checkout without the generated manuals, which aren't shipped anyway.
    assert build.MAKE_VARS == ("MAKEINFO=true",)


def test_notice_names_both_of_arms_downloads_the_build_and_the_sources():
    text = build.notice()
    arm64 = BINARIES[build.TARGET_FROM]
    for s in (arm64.url, arm64.sha256, SOURCE.url, SOURCE.sha256, SOURCE.archive, components.BUILD_SCRIPTS,
              "x86_64", "thumb/v6-m/nofp", "GPL-3.0-or-later", "COPYING.NEWLIB", "LGPL-3.0-or-later"):
        assert s in text
    assert "MinGW" not in text


def test_build_scripts_asset_holds_the_scripts_that_build_the_programs(tmp_path):
    dest = build.build_scripts(tmp_path / "dist")
    assert dest.name == components.BUILD_SCRIPTS
    with tarfile.open(dest) as tar:
        names = set(tar.getnames())
    top = components.BUILD_SCRIPTS.removesuffix(".tar")
    for script in ("arm_toolchain/build.py", "arm_toolchain/package.py", "kicad_bundle/gnu_build.py",
                   "pyproject.toml", "uv.lock"):
        assert f"{top}/simee-build/{script}" in names


def test_package_archives_a_built_root_with_the_given_notice(tmp_path):
    root = _tree(tmp_path / components.name(build.HOST), {"bin/arm-none-eabi-gcc": b"x86"})
    snapshot = targz(tmp_path / "s.tar.gz", {f"./{c}/COPYING": c.encode() for c in components.SHIPPED})
    out = package.package(root, snapshot, tmp_path / "dist", "notice", "tar.xz")
    assert out.name == f"{components.name(build.HOST)}.tar.xz"
    with tarfile.open(out) as tar:
        assert tar.extractfile(f"{root.name}/THIRD-PARTY.txt").read() == b"notice"


def test_intel_mac_programs_run_on_either_kind_of_mac(monkeypatch):
    from arm_toolchain import cli
    monkeypatch.setattr(cli, "this_machine", lambda: ("darwin", "arm64"))
    assert cli.runs_here("macos-x86_64") and cli.runs_here("macos-arm64") and not cli.runs_here("linux-arm64")
    monkeypatch.setattr(cli, "this_machine", lambda: ("linux", "x86_64"))
    assert not cli.runs_here("macos-x86_64") and cli.runs_here("linux-x86_64")
    assert set(cli.HOSTS) == {*BINARIES, build.HOST}


def test_sources_writes_the_snapshot_and_the_build_scripts(tmp_path, monkeypatch):
    from arm_toolchain import cli
    snapshot = targz(tmp_path / "cache" / SOURCE.archive, {"./gcc/COPYING": b"gcc"})
    monkeypatch.setattr(components, "fetch", lambda download, cache: snapshot)
    assert cli.main(["sources", "--out", str(tmp_path / "dist"), "--cache", str(tmp_path / "cache")]) == 0
    assert sorted(p.name for p in (tmp_path / "dist").iterdir()) == sorted([SOURCE.archive, components.BUILD_SCRIPTS])
