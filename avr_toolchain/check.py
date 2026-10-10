"""Prove a toolchain archive runs from any folder: unpack it into a temp dir (with a space in its path)
and compile simee-core's blink fixture for the ATmega328P as C, as C++ and with LTO, with an empty PATH,
so avr-gcc finds cc1, cc1plus, the LTO plugin, as, ld and avr-libc relative to itself or not at all."""

import tempfile
import zipfile
from pathlib import Path

from kicad_bundle.toolchain_check import assert_elf, runner, unpack

BLINK = Path(__file__).parent / "smoke" / "blink.c"
MCU = "atmega328p"
EM_AVR = 83
VARIANTS = {"c": [], "c++": ["-x", "c++"], "lto": ["-flto"]}

def assert_avr_elf(path: Path) -> None:
    assert_elf(path, EM_AVR, "AVR")


def _compile(root: Path, work: Path, variants: list[str]) -> None:
    """Compile blink as each variant with root's toolchain, in work, with an empty PATH."""
    run = runner(root, work)
    for variant in variants:
        elf = work / f"blink-{variant}.elf"
        run("avr-gcc", f"-mmcu={MCU}", "-Os", "-g", *VARIANTS[variant], "-o", elf, BLINK)
        assert_avr_elf(elf)
    if "c" in variants:
        hex_ = work / "blink.hex"
        run("avr-objcopy", "-O", "ihex", "-j", ".text", "-j", ".data", work / "blink-c.elf", hex_)
        lines = hex_.read_text().split()
        if len(lines) < 2 or lines[-1] != ":00000001FF":
            raise RuntimeError(f"avr-objcopy wrote no Intel HEX program: {lines[:3]}")


def check(archive: Path) -> None:
    windows = zipfile.is_zipfile(archive)
    # Windows: GCC hands collect2 lto-wrapper's path with each space escaped by a backslash, which
    # CreateProcess can't find, so -flto only works from a path without spaces (README). It's checked
    # from a copy in one.
    where = [("avr gcc check ", [v for v in VARIANTS if not (windows and v == "lto")])]
    if windows:
        where.append(("avr-gcc-check-", ["lto"]))
    for prefix, variants in where:
        with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
            _compile(unpack(archive, Path(tmp) / "copy"), Path(tmp), variants)
    print(f"  {archive.name}: compiles blink for the {MCU} (C, C++, LTO) from a copy with an empty PATH")
