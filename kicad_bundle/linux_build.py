"""Linux: build KiCad's own binaries from a simee/<version> branch and lay them over the official bundle.

The official kicad/kicad image is plain Debian trixie with Debian's libraries (KiCad's kicad-docker
Dockerfile.<x>-stable). So the branch is built on that very image, with Debian's archive as it was
when the image was made (snapshot.debian.org) and every installed package held: the -dev packages
are then those of the libraries the bundle ships, and the result links against exactly them. Only
KiCad's own files (kicad-cli, the kifaces, libki*) are replaced; the third-party closure,
its notices and its sources stay as linux.assemble made them.
"""

import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from kicad_bundle import bundle, debian
from kicad_bundle.bundle import KIFACES

# kicad-docker's build dependencies, less what only its QA run or library installs need.
BUILD_DEPS = (
    "build-essential cmake ninja-build pkg-config gettext swig protobuf-compiler libbz2-dev libcairo2-dev "
    "libglu1-mesa-dev libgl1-mesa-dev libglew-dev libx11-dev libwxgtk3.2-dev libwxgtk-webview3.2-dev "
    "mesa-common-dev python3-dev python3-wxgtk4.0 libboost-all-dev libglm-dev libcurl4-openssl-dev "
    "libgtk-3-dev libngspice0-dev ngspice-dev libocct-modeling-algorithms-dev libocct-modeling-data-dev "
    "libocct-data-exchange-dev libocct-visualization-dev libocct-foundation-dev libocct-ocaf-dev "
    "unixodbc-dev zlib1g-dev shared-mime-info libgit2-dev libsecret-1-dev libnng-dev libprotobuf-dev "
    "libzstd-dev libspnav-dev libpoppler-glib-dev"
)
# kicad-docker's cmake options (KICAD_BUILD_I18N only adds translations, which the bundle leaves out).
CMAKE_FLAGS = ("-G Ninja -DCMAKE_BUILD_TYPE=Release -DKICAD_SCRIPTING_WXPYTHON=ON -DKICAD_USE_OCC=ON "
               "-DKICAD_SPICE=ON -DKICAD_BUILD_I18N=OFF -DCMAKE_INSTALL_PREFIX=/usr -DKICAD_USE_CMAKE_FINDPROTOBUF=ON")
# The bundle's KiCad files: libexec/<binary> and lib/<library>.
BINARIES = ("kicad-cli", *(f"_{k}.kiface" for k in KIFACES))
NINJA_TARGETS = " ".join(("kicad-cli", *(f"{k}_kiface" for k in KIFACES)))
KICAD_LIBS = "libki*"
SOURCES = "dpkg-sources.txt"


def snapshot_stamp(when: float) -> str:
    return datetime.fromtimestamp(when, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def dockerfile(image: str, stamp: str) -> str:
    """Build kicad-cli and the kifaces (with the libki* they need) from kicad-src.tar.gz into
    /out, on image, with Debian's archive at stamp and the image's packages held."""
    snap = "deb [check-valid-until=no] https://snapshot.debian.org/archive"
    return f"""FROM {image}
USER root
RUN rm -f /etc/apt/sources.list.d/* && \\
    printf '%s\\n' '{snap}/debian/{stamp}/ trixie trixie-updates main' \\
                   '{snap}/debian-security/{stamp}/ trixie-security main' > /etc/apt/sources.list && \\
    apt-mark hold $(dpkg-query -W -f '${{Package}}\\n') > /dev/null && \\
    apt-get update && \\
    apt-get install -y --no-install-recommends {BUILD_DEPS}
COPY kicad-src.tar.gz /src/
RUN mkdir -p /src/kicad/build && tar -xzf /src/kicad-src.tar.gz -C /src/kicad --strip-components=1
WORKDIR /src/kicad/build
RUN cmake {CMAKE_FLAGS} .. && ninja {NINJA_TARGETS}
RUN mkdir /out && \\
    find . \\( {" -o ".join(f"-name {b}" for b in BINARIES)} -o -name '{KICAD_LIBS}.so.*' \\) -type f -exec cp {{}} /out/ \\; && \\
    strip --strip-unneeded /out/* && \\
    dpkg-query -W -f '${{Package}}\\t${{source:Package}}\\t${{source:Version}}\\n' > /out/{SOURCES}
"""


def _sources(rows: str) -> dict[str, tuple[str, str]]:
    return {pkg: (src, ver) for pkg, src, ver in (line.split("\t") for line in rows.splitlines() if line)}


def mismatched_sources(image: dict[str, tuple[str, str]], build: dict[str, tuple[str, str]]) -> list[tuple[str, str, str]]:
    """(source, image version, build version) for every Debian source both have at different versions:
    a -dev package from another version than the library the bundle ships."""
    shipped = dict(image.values())
    return sorted({(src, shipped[src], ver) for src, ver in build.values() if src in shipped and shipped[src] != ver})


def _unversioned(name: str) -> str:
    return name.split(".so", 1)[0]


def overlay(root: Path, built: Path) -> list[str]:
    """Replace the bundle's KiCad files (libexec/<binary>, lib/libki*) with those in built; returns the
    replaced paths. Refuses to leave any official KiCad file in place. A build of another version than the
    image's (a release-candidate rehearsal) names KiCad's libraries by its own version
    (libkicommon.so.10.0.7 for libkicommon.so.10.0.6): those replace the image's."""
    by_stem = {_unversioned(p.name): p.name for p in built.glob(f"{KICAD_LIBS}.so*")}
    targets = [f"libexec/{b}" for b in BINARIES]
    for official in sorted((root / "lib").glob(KICAD_LIBS)):
        if official.is_symlink():
            continue
        new = by_stem.get(_unversioned(official.name), official.name)
        if new != official.name and (built / new).is_file():
            official.unlink()
        targets.append(f"lib/{new}")
    return bundle.overlay(root, targets, built)


def build(image: str, rootfs: Path, src: Path, work: Path) -> Path:
    """Build the branch in src (a source tarball) on image (rootfs: its exported filesystem); returns
    the folder holding the binaries. Fails if a -dev package isn't the version of its library."""
    context, out = work / "linux-build", work / "linux-built"
    for d in (context, out):
        if d.exists():
            shutil.rmtree(d)
    context.mkdir(parents=True)
    stamp = snapshot_stamp((rootfs / debian.DPKG_STATUS).stat().st_mtime)
    (context / "Dockerfile").write_text(dockerfile(image, stamp))
    shutil.copy2(src, context / "kicad-src.tar.gz")
    tag = "simee-kicad-linux-build"
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-t", tag, str(context)], check=True)
    container = subprocess.run(["docker", "create", "--platform", "linux/amd64", tag],
                               capture_output=True, text=True, check=True).stdout.strip()
    try:
        subprocess.run(["docker", "cp", f"{container}:/out", str(out)], check=True)
    finally:
        subprocess.run(["docker", "rm", container], check=False, capture_output=True)
    image_sources = {p.name: (p.source, p.source_version)
                     for p in debian.packages((rootfs / debian.DPKG_STATUS).read_text()).values()}
    if bad := mismatched_sources(image_sources, _sources((out / SOURCES).read_text())):
        raise RuntimeError(f"built against other library versions than the image ships: {bad}")
    return out

