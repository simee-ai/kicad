"""Minimal Mach-O images for tests: a 64-bit header, LC_UUID and LC_BUILD_VERSION, optionally fat."""

import struct
import uuid as uuidlib

CPU = {"arm64": 0x0100000C, "x86_64": 0x01000007}


def thin(arch: str, uuid: str, minos: tuple[int, int] = (14, 0)) -> bytes:
    cmds = struct.pack("<II", 0x1B, 24) + uuidlib.UUID(uuid).bytes
    cmds += struct.pack("<IIIIII", 0x32, 24, 1, (minos[0] << 16) | (minos[1] << 8), 0, 0)
    return struct.pack("<IiiIIIII", 0xFEEDFACF, CPU[arch], 0, 6, 2, len(cmds), 0, 0) + cmds


def fat(*slices: tuple[str, bytes]) -> bytes:
    head = struct.pack(">II", 0xCAFEBABE, len(slices))
    offset, entries, body = 4096, b"", b""
    for arch, data in slices:
        entries += struct.pack(">iiIII", CPU[arch], 0, offset + len(body), len(data), 12)
        body += data
    return (head + entries).ljust(offset, b"\0") + body
