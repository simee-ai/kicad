"""vcpkg provenance of the DLLs in KiCad's Windows build. KiCad builds them in vcpkg's manifest mode
from its source tree's vcpkg.json (dependencies, version overrides) and vcpkg-configuration.json (the
registries and their baselines). A port's version is its override, or else the baseline of the
registry that serves it; the registry's versions/ database names the port's git tree, which holds the
portfile and patches. Each DLL names its port in the build paths vcpkg compiled it under
(C:\\vcpkg\\buildtrees\\<port>\\...), and some also name the source folder, whose hash covers the
source archive and the patches applied (see fingerprint)."""

import hashlib
import io
import json
import re
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from kicad_bundle.fetch import Fetch
from kicad_bundle.portfile import Download

KICAD_SOURCE = "https://raw.githubusercontent.com/KiCad/kicad-source-mirror"
_BUILT = re.compile(rb"\\buildtrees\\([\w.+-]+)\\")
_SOURCE_DIR = re.compile(rb"\\buildtrees\\([\w.+-]+)\\src\\[^\\\0]*-([0-9a-f]{10})\.clean\\")
_VERSION_KEYS = ("version", "version-semver", "version-date", "version-string")


@dataclass(frozen=True)
class Registry:
    url: str
    baseline: str  # commit
    packages: tuple[str, ...] = ()  # the ports it serves; none for the default registry


@dataclass(frozen=True)
class Config:
    default: Registry
    registries: tuple[Registry, ...]
    overrides: dict[str, tuple[str, int]] = field(hash=False)

    def registry(self, port: str) -> Registry:
        return next((r for r in self.registries if port in r.packages), self.default)


@dataclass(frozen=True)
class Port:
    name: str
    version: str
    port_version: int
    registry: Registry
    tree: str  # git tree id of the port folder
    files: dict[str, bytes] = field(hash=False, compare=False)  # portfile.cmake, patches, ...


def _version(text: str) -> tuple[str, int]:
    version, _, port_version = text.partition("#")
    return version, int(port_version or 0)


def config(manifest: str, configuration: str) -> Config:
    """KiCad's vcpkg.json and vcpkg-configuration.json."""
    m, c = json.loads(manifest), json.loads(configuration)

    def registry(r: dict) -> Registry:
        if r.get("kind") != "git":
            raise RuntimeError(f"vcpkg registry {r}: only git registries are supported")
        return Registry(r["repository"], r["baseline"], tuple(r.get("packages", ())))

    overrides = {}
    for o in m.get("overrides", ()):
        version, port_version = _version(next(o[k] for k in _VERSION_KEYS if k in o))
        overrides[o["name"]] = (version, o.get("port-version", port_version))
    return Config(registry(c["default-registry"]), tuple(registry(r) for r in c.get("registries", ())), overrides)


def kicad_config(version: str, fetch: Fetch) -> Config:
    """The vcpkg setup in KiCad's source at the release tag, which its Windows build used."""
    return config(*(fetch(f"{KICAD_SOURCE}/{version}/{name}").decode()
                    for name in ("vcpkg.json", "vcpkg-configuration.json")))


class GitRepo:
    """A shallow, treeless clone of a registry: it fetches the commits it's asked about, and git
    fetches the trees and files read from them (or from any older commit, by id) when first needed."""

    def __init__(self, url: str, cache: Path):
        self.url = url
        self.dir = cache / re.sub(r"[^\w.-]+", "_", url.split("://", 1)[-1]).strip("_")

    def _git(self, *args: str) -> bytes:
        # The files are hashed, so read them as committed: no CRLF conversion (Windows' git defaults to
        # core.autocrlf=true, and git archive applies it).
        return subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(self.dir), *args],
                              capture_output=True, check=True).stdout

    def _commit(self, commit: str) -> None:
        if not self.dir.exists():
            self.dir.mkdir(parents=True)
            self._git("init", "-q", "--bare")
            self._git("remote", "add", "origin", self.url)
        try:
            self._git("cat-file", "-e", f"{commit}^{{commit}}")
        except subprocess.CalledProcessError:
            self._git("fetch", "-q", "--filter=tree:0", "--depth", "1", "origin", commit)

    def show(self, commit: str, path: str) -> bytes:
        self._commit(commit)
        return self._git("show", f"{commit}:{path}")

    def tree(self, tree: str) -> dict[str, bytes]:
        """Every file under a tree (by its id), by path."""
        with tarfile.open(fileobj=io.BytesIO(self._git("archive", "--format=tar", tree))) as tar:
            return {m.name: tar.extractfile(m).read() for m in tar if m.isfile()}


def port(config: Config, name: str, repo: Callable[[Registry], GitRepo]) -> Port:
    """The port KiCad's build used: its version and its folder in the registry that serves it."""
    registry = config.registry(name)
    git = repo(registry)
    if name in config.overrides:
        version, port_version = config.overrides[name]
    else:
        base = json.loads(git.show(registry.baseline, "versions/baseline.json"))["default"].get(name)
        if base is None:
            raise RuntimeError(f"vcpkg port {name}: not in the baseline of {registry.url}")
        version, port_version = base["baseline"], base.get("port-version", 0)
    versions = json.loads(git.show(registry.baseline, f"versions/{name[0]}-/{name}.json"))["versions"]
    tree = next((v["git-tree"] for v in versions if v.get("port-version", 0) == port_version
                 and next((v[k] for k in _VERSION_KEYS if k in v), None) == version), None)
    if tree is None:
        raise RuntimeError(f"vcpkg port {name} {version}#{port_version}: not in {registry.url}'s versions at "
                           f"{registry.baseline[:10]}")
    return Port(name, version, port_version, registry, tree, git.tree(tree))


def built_ports(data: bytes) -> set[str]:
    """The vcpkg ports a binary was compiled in (their build paths: PDB path, __FILE__ strings)."""
    return {m.group(1).decode() for m in _BUILT.finditer(data)}


def source_dirs(data: bytes) -> set[tuple[str, str]]:
    """(port, hash) of the vcpkg source folders (<ref>-<hash>.clean) a binary names."""
    return {(m.group(1).decode(), m.group(2).decode()) for m in _SOURCE_DIR.finditer(data)}


def fingerprint(download: Download, files: dict[str, bytes]) -> str | None:
    """The hash vcpkg names a download's source folder by: the SHA512 of the archive's SHA512 followed
    by each patch's. None if a patch isn't a file of the port (vcpkg made it during the build)."""
    if any(p not in files for p in download.patches):
        return None
    text = download.sha512 + "".join(hashlib.sha512(files[p]).hexdigest() for p in download.patches)
    return hashlib.sha512(text.encode()).hexdigest()[:10]
