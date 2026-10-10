"""Release bookkeeping for the arm-gcc workflow: tag numbering, checksums and notes.

    python -m arm_toolchain.publish --dist dist --tags tags.txt --run-url URL

writes dist/SHA256SUMS and notes.md and prints the release tag (arm-gcc-<Arm release>-<n>).
"""

from arm_toolchain.components import DOWNLOADS, GCC_VERSION, MULTILIBS, RELEASE, SOURCE
from kicad_bundle import publish

PREFIX = "arm-gcc"


def next_tag(release: str, existing: list[str]) -> str:
    return publish.next_tag(release, existing, PREFIX)


def release_notes(run_url: str, sums: str) -> str:
    return f"""Arm GNU Toolchain {RELEASE} (GCC {GCC_VERSION}, arm-none-eabi) for simee-core's package: Arm's own
builds from {DOWNLOADS}/binrel/, unmodified, trimmed to what an RP2040 build runs and links (the C and C++
drivers, binutils, cc1, cc1plus, LTO, and newlib, libstdc++ and libgcc for the {", ".join(MULTILIBS)}
multilib), about 185 MB of the 1 GB release.

Each archive has one top folder, the toolchain root: `bin/arm-none-eabi-gcc` (`bin\\arm-none-eabi-gcc.exe`
on Windows), `bin/arm-none-eabi-objcopy` and the rest of binutils, `arm-none-eabi/`, `lib/gcc/arm-none-eabi/`.
It runs from wherever it is copied: macOS arm64, Linux x86_64 and arm64, Windows x86_64, as Arm's own
builds do. Arm builds no macOS x86_64 toolchain after 14.2.rel1, so there is none here. `THIRD-PARTY.txt`
lists the components and their licences, whose texts are in `share/doc/`.

Sources: `{SOURCE.archive}` is Arm's source snapshot for the release
({SOURCE.url}), unchanged.

SHA256 (also in `SHA256SUMS`), to pin in simee-core's `cmake/SimeePicoSdk.cmake`:

```
{sums.strip()}
```

Built by {run_url}
"""


def main(argv=None) -> int:
    return publish.toolchain_main(argv, RELEASE, PREFIX, release_notes)


if __name__ == "__main__":
    raise SystemExit(main())
