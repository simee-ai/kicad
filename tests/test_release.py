import io
import json
import urllib.error

import pytest

from kicad_bundle import release
from kicad_bundle.release import Installer

API = "https://api.github.com/repos/KiCad/kicad-source-mirror/releases"
S3 = "https://kicad-downloads.s3.cern.ch"
DMG = "osx/stable/kicad-unified-universal-{v}.dmg"
EXE = "windows/stable/kicad-{v}-x86_64.exe"


def listing(*objects: tuple[str, str]) -> bytes:
    """An S3 ListObjects reply holding (key, last modified) objects."""
    contents = "".join(f"<Contents><Key>{key}</Key><LastModified>{when}</LastModified><Size>1</Size></Contents>"
                       for key, when in objects)
    return (f'<?xml version="1.0" encoding="UTF-8"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/'
            f'2006-03-01/"><Name>kicad-downloads</Name>{contents}</ListBucketResult>').encode()


def not_found(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"message": "Not Found"}'))


def server(replies: dict[str, bytes]):
    """A fetch serving replies, and GitHub's 404 for any other release."""
    def fetch(url: str) -> bytes:
        if url in replies:
            return replies[url]
        if url.startswith(f"{API}/tags/"):
            raise not_found(url)
        raise AssertionError(url)
    return fetch


GITHUB_10_0_6 = json.dumps({"published_at": "2026-08-29T15:43:28Z", "assets": [
    {"name": n, "browser_download_url": f"https://github.com/KiCad/kicad-source-mirror/releases/download/10.0.6/{n}"}
    for n in ("kicad-10.0.6-arm64.exe", "kicad-10.0.6-x86_64.exe", "kicad-unified-universal-10.0.6.dmg")]}).encode()


@pytest.mark.parametrize("pattern, download, name", [
    ("kicad-unified-universal-*.dmg", DMG, "kicad-unified-universal-10.0.6.dmg"),
    ("kicad-10.0.6-x86_64.exe", EXE, "kicad-10.0.6-x86_64.exe")])
def test_a_stable_version_comes_from_its_github_release(pattern, download, name):
    fetch = server({f"{API}/tags/10.0.6": GITHUB_10_0_6})
    assert release.installer("10.0.6", pattern, download.format(v="10.0.6"), fetch) == Installer(
        f"https://github.com/KiCad/kicad-source-mirror/releases/download/10.0.6/{name}", "2026-08-29T15:43:28Z")


@pytest.mark.parametrize("pattern, download", [("kicad-unified-universal-*.dmg", DMG),
                                               ("kicad-10.0.7-rc2-x86_64.exe", EXE)])
def test_a_version_without_a_github_release_comes_from_the_download_server(pattern, download):
    path = download.format(v="10.0.7-rc2")
    fetch = server({f"{S3}/?prefix={path}": listing((path, "2026-10-03T04:31:27.549Z"))})
    assert release.installer("10.0.7-rc2", pattern, path, fetch) == Installer(f"{S3}/{path}", "2026-10-03T04:31:27Z")


def test_the_download_server_object_must_be_the_one_named_not_just_one_starting_with_it():
    path = DMG.format(v="10.0.1")
    fetch = server({f"{S3}/?prefix={path}": listing((DMG.format(v="10.0.1") + ".sig", "2026-04-15T21:09:22.332Z"))})
    with pytest.raises(RuntimeError, match="no GitHub release.*doesn't exist"):
        release.installer("10.0.1", "kicad-unified-universal-*.dmg", path, fetch)


def test_other_github_errors_are_not_taken_for_a_missing_release():
    def fetch(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 403, "rate limited", {}, io.BytesIO(b""))
    with pytest.raises(urllib.error.HTTPError):
        release.installer("10.0.6", "kicad-unified-universal-*.dmg", DMG.format(v="10.0.6"), fetch)


def test_cached_downloads_into_the_versions_folder_once(tmp_path):
    urls = []

    def stream(url: str) -> io.BytesIO:
        urls.append(url)
        return io.BytesIO(b"dmg")
    found = Installer(f"{S3}/{DMG.format(v='10.0.7-rc2')}", "2026-10-03T04:31:27Z")
    for _ in range(2):
        path = release.cached(found, "10.0.7-rc2", tmp_path, stream)
    assert path == tmp_path / "10.0.7-rc2" / "kicad-unified-universal-10.0.7-rc2.dmg"
    assert path.read_bytes() == b"dmg" and urls == [found.url]
