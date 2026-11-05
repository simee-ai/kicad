"""Pin immutable source metadata from a checksum-verified published source archive.

python -m kicad_bundle.debian_pins sources.tar kicad_bundle/debian-sources.json --sha256 <SHA256SUMS entry> --url <asset URL>
"""

import argparse
import hashlib
import json
import tarfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

from kicad_bundle.debian import descriptor, descriptor_files
from kicad_bundle.fetch import write_atomically


def generate(archive: Path, dest: Path, sha256: str, url: str) -> None:
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != sha256:
            raise ValueError("source archive checksum mismatch")
    groups = defaultdict(dict)
    descriptors = {}
    with tarfile.open(archive) as tar:
        for member in tar:
            if not member.isfile():
                continue
            path = PurePosixPath(member.name)
            with tar.extractfile(member) as stream:
                digest = hashlib.file_digest(stream, "sha1").hexdigest()
            if path.name in groups[path.parent]:
                raise ValueError(f"duplicate source file: {path}")
            groups[path.parent][path.name] = digest
            if path.suffix == ".dsc":
                if path.parent in descriptors:
                    raise ValueError(f"multiple descriptors: {path.parent}")
                descriptors[path.parent] = tar.extractfile(member).read().decode("utf-8")
    packages = {}
    if not groups or groups.keys() != descriptors.keys():
        raise ValueError("source archive has missing descriptors")
    for folder, files in groups.items():
        source, version, required = descriptor(descriptors[folder])
        files = descriptor_files(files, required)
        if files is None or not source or not version:
            raise ValueError(f"source descriptor checksum mismatch: {folder}")
        versions = packages.setdefault(source, {})
        if version in versions:
            raise ValueError(f"duplicate source version: {source} {version}")
        versions[version] = files
    data = {"provenance": {"url": url, "sha256": sha256}, "packages": packages}
    write_atomically(dest, lambda stream: stream.write((json.dumps(data, indent=2, sort_keys=True) + "\n").encode()))
    print(f"Pinned {sum(map(len, packages.values()))} exact source versions")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    generate(args.archive, args.dest, args.sha256, args.url)
