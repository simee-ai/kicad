import hashlib
import io
import json
import subprocess
import sys
import tarfile

import pytest


@pytest.mark.parametrize("fault", [None, "archive", "source", "alias", "extra"])
def test_pin_generator_verifies_the_archive_and_descriptor_checksums(tmp_path, fault):
    source = b"original source bytes"
    digest = hashlib.sha1(source).hexdigest()
    dsc = ("-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
           "Format: 3.0 (quilt)\nSource: example\nVersion: 2:1.0-1\nChecksums-Sha1:\n"
           f" {digest} {len(source)} example_1.0.orig.tar.xz\n\n-----BEGIN PGP SIGNATURE-----\n").encode()
    archive = tmp_path / "sources.tar"
    with tarfile.open(archive, "w") as tar:
        files = {"example_1.0-1.dsc": dsc,
                 "old_0.9.orig.tar.xz" if fault == "alias" else "example_1.0.orig.tar.xz":
                     b"tampered" if fault == "source" else source}
        if fault == "extra":
            files["example_1.0-1.git.tar.xz"] = b"extra upstream metadata"
        for name, data in files.items():
            member = tarfile.TarInfo(f"sources/example_1.0-1/{name}")
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest() if fault != "archive" else "0" * 64
    dest = tmp_path / "pins.json"
    result = subprocess.run([sys.executable, "-m", "kicad_bundle.debian_pins", str(archive), str(dest),
                             "--sha256", checksum, "--url", "https://example.test/sources.tar"],
                            capture_output=True, text=True, timeout=10)
    if fault in ("archive", "source"):
        assert result.returncode != 0
        assert "checksum" in result.stderr.lower()
        assert not dest.exists()
    else:
        assert result.returncode == 0, result.stderr
        pins = json.loads(dest.read_text())
        assert pins["provenance"] == {"url": "https://example.test/sources.tar", "sha256": checksum}
        expected = {"example_1.0-1.dsc": hashlib.sha1(dsc).hexdigest(), "example_1.0.orig.tar.xz": digest}
        if fault == "extra":
            expected["example_1.0-1.git.tar.xz"] = hashlib.sha1(b"extra upstream metadata").hexdigest()
        assert pins["packages"] == {"example": {"2:1.0-1": expected}}
