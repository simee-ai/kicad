"""Prove a toolchain archive runs from any folder: unpack it into a temp dir (with a space in its path)
and compile simee-core's blink fixture for the ATmega328P as C, as C++ and with LTO, with an empty PATH,
so avr-gcc finds cc1, cc1plus, the LTO plugin, as, ld and avr-libc relative to itself or not at all."""

import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

BLINK = Path(__file__).parent / "smoke" / "blink.c"
MCU = "atmega328p"
EM_AVR = 83
VARIANTS = {"c": [], "c++": ["-x", "c++"], "lto": ["-flto"]}


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


def assert_avr_elf(path: Path) -> None:
    head = path.read_bytes()[:20]
    if head[:4] != b"\x7fELF" or int.from_bytes(head[18:20], "little") != EM_AVR:
        raise RuntimeError(f"{path.name} is not an AVR ELF file")


def check(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="avr gcc check ") as tmp:
        work = Path(tmp)
        root = unpack(archive, work / "copy")
        exe = ".exe" if (root / "bin" / "avr-gcc.exe").exists() else ""
        env = {"PATH": ""}
        if "SYSTEMROOT" in os.environ:  # Windows needs it to start any program
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]

        def run(tool: str, *args) -> None:
            done = subprocess.run([str(root / "bin" / f"{tool}{exe}"), *map(str, args)], cwd=work, env=env,
                                  capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError(f"{tool} {' '.join(map(str, args))} failed:\n{done.stdout}{done.stderr}")

        for variant, flags in VARIANTS.items():
            elf = work / f"blink-{variant}.elf"
            run("avr-gcc", f"-mmcu={MCU}", "-Os", "-g", *flags, "-o", elf, BLINK)
            assert_avr_elf(elf)
        hex_ = work / "blink.hex"
        run("avr-objcopy", "-O", "ihex", "-j", ".text", "-j", ".data", work / "blink-c.elf", hex_)
        lines = hex_.read_text().split()
        if len(lines) < 2 or lines[-1] != ":00000001FF":
            raise RuntimeError(f"avr-objcopy wrote no Intel HEX program: {lines[:3]}")
    print(f"  {archive.name}: compiles blink for the {MCU} (C, C++, LTO) from a copy with an empty PATH")
