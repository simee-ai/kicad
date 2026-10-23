# simee-ai/kicad

Builds of [KiCad](https://www.kicad.org) and of the GNU toolchains for AVR and Arm microcontrollers, published as
this repository's releases.

## Licences

- `cli-<version>-<n>`: a trimmed `kicad-cli` from that KiCad release, with KiCad's own files built from the official
  release or from this repository's `simee/<version>` branch (KiCad's source with simee's changes, named in the
  release notes by commit). KiCad is GPL-3.0-or-later (its `LICENSE.README` lists the third-party code it includes),
  and so is each bundle as a whole. The bundled third-party libraries keep their own licences: each bundle's
  `THIRD-PARTY.txt` names them, with their licence files beside it. The Windows bundle also holds Microsoft's
  Visual C++ runtime, shipped as Visual Studio 2022 Distributable Code under Microsoft's terms.
- `avr-gcc-<version>-<n>`: binutils and GCC (GPL-3.0-or-later; libgcc under the GCC Runtime Library Exception, so
  firmware built with it carries no GPL obligation), GMP, MPFR and MPC (LGPL-3.0-or-later) and avr-libc
  (BSD-3-Clause).
- `arm-gcc-<version>-<n>`: Arm GNU Toolchain: binutils and GCC (GPL-3.0-or-later; libgcc and libstdc++ under the
  GCC Runtime Library Exception), newlib (the BSD-style licences its `COPYING.NEWLIB` lists), GMP, MPFR and MPC
  (LGPL-3.0-or-later), isl (MIT), libiconv (LGPL-2.1-or-later) and zstd (BSD-3-Clause).

Each toolchain archive holds the licence files of its components in `share/doc/` and a `THIRD-PARTY.txt` naming
them. Every release carries the complete corresponding source of everything it ships, attached to the release
itself, rather than a written offer.
