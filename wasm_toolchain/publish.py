"""Release bookkeeping for the wasi-sdk workflow: tag numbering, checksums and notes.

    python -m wasm_toolchain.publish --dist dist --tags tags.txt --run-url URL

writes dist/SHA256SUMS and notes.md and prints the release tag (wasi-sdk-<version>-<n>).
"""

from kicad_bundle import publish
from wasm_toolchain.components import DOWNLOADS, SHIPPED_IN_APP, VERSION
from wasm_toolchain.package import TARGET

PREFIX = "wasi-sdk"


def next_tag(version: str, existing: list[str]) -> str:
    return publish.next_tag(version, existing, PREFIX)


def release_notes(run_url: str, sums: str) -> str:
    return f"""wasi-sdk {VERSION} for simee-core's build and package: the WebAssembly project's own builds from
{DOWNLOADS}/, unmodified, trimmed to what compiles and links C++ into a {TARGET} module
with C++ exceptions: clang (as clang and clang++), lld (as wasm-ld), the libraries they load, clang's headers
and builtins, and the {TARGET} sysroot (wasi-libc, and libc++, libc++abi and libunwind built with exception
handling), about 150 MB of the 600 MB release. On Windows, Microsoft's C++ runtime the programs load is shipped
next to them.

Each archive has one top folder, the toolchain root: `bin/clang++` (`bin\\clang++.exe` on Windows),
`bin/wasm-ld`, `lib/`, `share/wasi-sysroot/`. It runs from wherever it is copied. simee's app ships the
archives of {", ".join(SHIPPED_IN_APP)} packed and unpacks one when a project's C++ model is first compiled;
linux-arm64 is for simee-core's own build only. `THIRD-PARTY.txt` lists the components and their licences, whose
texts are in `share/doc/`.

SHA256 (also in `SHA256SUMS`), to pin in simee-core's `cmake/SimeeWasm.cmake`:

```
{sums.strip()}
```

Built by {run_url}
"""


def main(argv=None) -> int:
    return publish.toolchain_main(argv, VERSION, PREFIX, release_notes)


if __name__ == "__main__":
    raise SystemExit(main())
