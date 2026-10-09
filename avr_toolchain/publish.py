"""Release bookkeeping for the avr-gcc workflow: tag numbering, checksums and notes.

    python -m avr_toolchain.publish --dist dist --tags tags.txt --run-url URL

writes dist/SHA256SUMS and notes.md and prints the release tag (avr-gcc-<gcc version>-<n>).
"""

import argparse
from pathlib import Path

from avr_toolchain import components
from avr_toolchain.components import SOURCES, VERSION
from kicad_bundle import publish

PREFIX = "avr-gcc"


def next_tag(version: str, existing: list[str]) -> str:
    return publish.next_tag(version, existing, PREFIX)


def release_notes(run_url: str, sums: str) -> str:
    built = "\n".join(f"- {c.name} {c.version} ({c.licence}): {c.url}" for c in SOURCES)
    return f"""AVR toolchain (avr-gcc, binutils, avr-libc) for simee-core's package, built from unmodified upstream sources:

{built}

Each archive has one top folder, the toolchain root: `bin/avr-gcc` (`bin\\avr-gcc.exe` on Windows),
`bin/avr-objcopy` and the rest of binutils, `avr/include`, `avr/lib`, `lib/gcc/avr/{VERSION}/`. It runs
from wherever it is copied, with nothing else installed: macOS 11 or later (arm64, x86_64), Linux x86_64
with glibc 2.36 or later, Windows 10 or later (x86_64). `THIRD-PARTY.txt` lists the components and their
licences, whose texts are in `share/doc/`.

Sources: `{components.SOURCE_ARCHIVE}` holds every upstream archive above and the scripts that built the
toolchain; `{components.RUNTIME_SOURCES}` the exact Debian sources of the C/C++ runtime the Linux and
Windows programs link statically.

SHA256 (also in `SHA256SUMS`), to pin in simee-core's `cmake/SimeeAvrGcc.cmake`:

```
{sums.strip()}
```

Built by {run_url}
"""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--tags", type=Path, required=True, help="file with existing release tags, one per line")
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--notes", type=Path, default=Path("notes.md"))
    args = parser.parse_args(argv)

    sums = publish.sha256sums(args.dist)
    (args.dist / "SHA256SUMS").write_text(sums)
    args.notes.write_text(release_notes(args.run_url, sums))
    print(next_tag(VERSION, args.tags.read_text().split()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
