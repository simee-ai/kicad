# simee-ai/kicad

Builds of [KiCad](https://www.kicad.org), published as this repository's releases.

The AVR, Arm and WebAssembly compilers are built and published separately.
[Toolchain releases](https://github.com/simee-ai/toolchains/releases)

## Licences

- `cli-<version>-<n>`: a trimmed `kicad-cli` from that KiCad release, with KiCad's own files built from the official
  release or from this repository's `simee/<version>` branch (KiCad's source with simee's changes, named in the
  release notes by commit). KiCad is GPL-3.0-or-later (its `LICENSE.README` lists the third-party code it includes),
  and so is each bundle as a whole. The bundled third-party libraries keep their own licences: each bundle's
  `THIRD-PARTY.txt` names them, with their licence files beside it. The Windows bundle also holds Microsoft's
  Visual C++ runtime, shipped as Visual Studio 2022 Distributable Code under Microsoft's terms.
