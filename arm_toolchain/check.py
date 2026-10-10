"""Prove a toolchain archive runs from any folder: unpack it into a temp dir (with a space in its path)
and, with an empty PATH, compile and link a C and a C++ program for the RP2040's Cortex-M0+ against
newlib, libstdc++ and libgcc, and make a raw binary of each, so arm-none-eabi-gcc finds cc1, cc1plus,
as, ld, the headers and the multilib's libraries relative to itself or not at all."""

import tempfile
from pathlib import Path

from arm_toolchain.components import MULTILIBS
from kicad_bundle.toolchain_check import assert_elf, runner, unpack

SMOKE = Path(__file__).parent / "smoke"
EM_ARM = 40
CPU = ["-mcpu=cortex-m0plus", "-mthumb", "-mfloat-abi=soft"]
# newlib's stubs for the system calls (nosys.specs), as a bare-metal program without the pico-sdk's.
FLAGS = [*CPU, "-Os", "--specs=nosys.specs"]
PROGRAMS = {"main.c": ("arm-none-eabi-gcc", []),
            "main.cpp": ("arm-none-eabi-g++", ["-fno-exceptions", "-fno-rtti"])}


def assert_arm_elf(path: Path) -> None:
    assert_elf(path, EM_ARM, "Arm")


def _compile(root: Path, work: Path) -> None:
    run = runner(root, work)
    multilib = run("arm-none-eabi-gcc", *CPU, "-print-multi-directory").strip()
    if multilib != MULTILIBS[0]:
        raise RuntimeError(f"arm-none-eabi-gcc picks the {multilib} multilib for the Cortex-M0+, not {MULTILIBS[0]}")
    for source, (driver, flags) in PROGRAMS.items():
        elf = work / f"{source}.elf"
        run(driver, *FLAGS, *flags, "-o", elf, SMOKE / source)
        assert_arm_elf(elf)
        image = work / f"{source}.bin"
        run("arm-none-eabi-objcopy", "-O", "binary", elf, image)
        if not image.stat().st_size:
            raise RuntimeError(f"arm-none-eabi-objcopy made an empty image of {source}")


def check(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="arm gcc check ") as tmp:
        _compile(unpack(archive, Path(tmp) / "copy"), Path(tmp))
    print(f"  {archive.name}: compiles and links C and C++ for the Cortex-M0+ from a copy with an empty PATH")
