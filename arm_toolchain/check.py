"""Prove a toolchain archive runs from any folder: unpack it into a temp dir (with a space in its path)
and, with an empty PATH, compile and link a C and a C++ program for each kept multilib's chip (the
RP2040's Cortex-M0+, the RP2350's Cortex-M33) against newlib, libstdc++ and libgcc, and make a raw binary
of each, so arm-none-eabi-gcc finds cc1, cc1plus, as, ld, the headers and the multilib's libraries
relative to itself or not at all."""

import tempfile
from pathlib import Path

from arm_toolchain.components import CHIPS, MULTILIBS
from kicad_bundle.toolchain_check import assert_elf, runner, unpack

SMOKE = Path(__file__).parent / "smoke"
EM_ARM = 40
# newlib's stubs for the system calls (nosys.specs), as a bare-metal program without the pico-sdk's.
FLAGS = ["-Os", "--specs=nosys.specs"]
PROGRAMS = {"main.c": ("arm-none-eabi-gcc", []),
            "main.cpp": ("arm-none-eabi-g++", ["-fno-exceptions", "-fno-rtti"])}


def assert_arm_elf(path: Path) -> None:
    assert_elf(path, EM_ARM, "Arm")


def _compile(root: Path, work: Path) -> None:
    run = runner(root, work)
    for wanted, target in MULTILIBS.items():
        multilib = run("arm-none-eabi-gcc", *target.flags, "-print-multi-directory").strip()
        if multilib != wanted:
            raise RuntimeError(f"arm-none-eabi-gcc picks the {multilib} multilib for the {target.chip}, not {wanted}")
        for source, (driver, flags) in PROGRAMS.items():
            out = f"{source}-{multilib.replace('/', '-')}"
            elf = work / f"{out}.elf"
            run(driver, *target.flags, *FLAGS, *flags, "-o", elf, SMOKE / source)
            assert_arm_elf(elf)
            image = work / f"{out}.bin"
            run("arm-none-eabi-objcopy", "-O", "binary", elf, image)
            if not image.stat().st_size:
                raise RuntimeError(f"arm-none-eabi-objcopy made an empty image of {source} for the {target.chip}")


def check(archive: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="arm gcc check ") as tmp:
        _compile(unpack(archive, Path(tmp) / "copy"), Path(tmp))
    print(f"  {archive.name}: compiles and links C and C++ for the {CHIPS} from a copy with an empty PATH")
