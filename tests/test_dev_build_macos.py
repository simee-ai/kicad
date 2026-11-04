"""dev/build-macos-homebrew.sh, run against stub xcode-select/brew/cmake/ninja (simee-kicad#49)."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "dev" / "build-macos-homebrew.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(0o755)


def _configure_args(tmp_path: Path, developer_dir: Path) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "cmake.args"
    _stub(bin_dir, "xcode-select", f"echo {developer_dir}")
    _stub(bin_dir, "brew", f"echo {tmp_path / 'brew'}")
    _stub(bin_dir, "cmake", f'printf "%s\\n" "$@" > {log}')
    _stub(bin_dir, "ninja", "true")
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    subprocess.run(["sh", str(SCRIPT), str(tmp_path / "src"), str(tmp_path / "build")], env=env, check=True)
    return log.read_text().splitlines()


def _clang(bin_dir: Path) -> Path:
    bin_dir.mkdir(parents=True)
    for name in ("clang", "clang++"):
        (bin_dir / name).write_text("")
        (bin_dir / name).chmod(0o755)
    return bin_dir


def test_uses_xcodes_toolchain_when_xcode_is_installed(tmp_path):
    developer = tmp_path / "Xcode.app" / "Contents" / "Developer"
    tc = _clang(developer / "Toolchains" / "XcodeDefault.xctoolchain" / "usr" / "bin")
    _clang(developer / "usr" / "bin")
    args = _configure_args(tmp_path, developer)
    assert f"-DCMAKE_C_COMPILER={tc}/clang" in args
    assert f"-DCMAKE_CXX_COMPILER={tc}/clang++" in args


def test_falls_back_to_the_command_line_tools_clang(tmp_path):
    developer = tmp_path / "CommandLineTools"
    tc = _clang(developer / "usr" / "bin")
    args = _configure_args(tmp_path, developer)
    assert f"-DCMAKE_C_COMPILER={tc}/clang" in args
    assert f"-DCMAKE_CXX_COMPILER={tc}/clang++" in args
    assert f"-DCMAKE_OBJCXX_COMPILER={tc}/clang++" in args
