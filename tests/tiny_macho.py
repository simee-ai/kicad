"""Minimal Mach-O images for tests: a 64-bit header, LC_UUID and LC_BUILD_VERSION, optionally an
LC_ID_DYLIB and LC_LOAD_DYLIBs, optionally fat."""

import struct
import uuid as uuidlib

CPU = {"arm64": 0x0100000C, "x86_64": 0x01000007}
LC_ID_DYLIB, LC_LOAD_DYLIB = 0x0D, 0x0C


def _version(text: str) -> int:
    major, minor, patch = (int(p) for p in (text.split(".") + ["0", "0"])[:3])
    return (major << 16) | (minor << 8) | patch


def _dylib(cmd: int, name: str, current: str = "1.0.0") -> bytes:
    path = name.encode() + b"\0"
    size = (24 + len(path) + 7) // 8 * 8
    return struct.pack("<IIIIII", cmd, size, 24, 2, _version(current), _version("1.0.0")) + path.ljust(size - 24, b"\0")


def thin(arch: str, uuid: str, minos: tuple[int, int] = (14, 0), dylib: tuple[str, str] | None = None,
         deps: tuple[str, ...] = ()) -> bytes:
    """dylib: (install name, current version) of a library's LC_ID_DYLIB; deps: install names it loads."""
    cmds = [struct.pack("<II", 0x1B, 24) + uuidlib.UUID(uuid).bytes,
            struct.pack("<IIIIII", 0x32, 24, 1, (minos[0] << 16) | (minos[1] << 8), 0, 0)]
    if dylib:
        cmds.append(_dylib(LC_ID_DYLIB, *dylib))
    cmds += [_dylib(LC_LOAD_DYLIB, d) for d in deps]
    body = b"".join(cmds)
    return struct.pack("<IiiIIIII", 0xFEEDFACF, CPU[arch], 0, 6, len(cmds), len(body), 0, 0) + body


def fat(*slices: tuple[str, bytes]) -> bytes:
    head = struct.pack(">II", 0xCAFEBABE, len(slices))
    offset, entries, body = 4096, b"", b""
    for arch, data in slices:
        entries += struct.pack(">iiIII", CPU[arch], 0, offset + len(body), len(data), 12)
        body += data
    return (head + entries).ljust(offset, b"\0") + body
