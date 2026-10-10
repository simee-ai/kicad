"""What the AVR and Arm toolchain checks share: unpack an archive somewhere else and run its programs
with an empty PATH, so they find their own parts relative to themselves or not at all."""

import os
import subprocess
import tarfile
import zipfile
from pathlib import Path
from typing import Callable


def unpack(archive: Path, dest: Path) -> Path:
    """The toolchain root (the archive's one top folder), unpacked under dest."""
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as tar:
            tar.extractall(dest, filter="tar")
    [root] = [p for p in dest.iterdir() if p.is_dir()]
    return root


def assert_elf(path: Path, machine: int, what: str) -> None:
    """path is an ELF file for e_machine machine (what: its name, for the error)."""
    head = path.read_bytes()[:20]
    if head[:4] != b"\x7fELF" or int.from_bytes(head[18:20], "little") != machine:
        raise RuntimeError(f"{path.name} is not an {what} ELF file")


def runner(root: Path, work: Path) -> Callable[..., str]:
    """run(tool, *args): root/bin/<tool> (.exe on Windows) in work with an empty PATH; its stdout, or
    RuntimeError with its output when it fails."""
    exe = ".exe" if any((root / "bin").glob("*.exe")) else ""
    env = {"PATH": ""}
    if "SYSTEMROOT" in os.environ:  # Windows needs it to start any program
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

    def run(tool: str, *args) -> str:
        done = subprocess.run([str(root / "bin" / f"{tool}{exe}"), *map(str, args)], cwd=work, env=env,
                              capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f"{tool} {' '.join(map(str, args))} failed:\n{done.stdout}{done.stderr}")
        return done.stdout

    return run
