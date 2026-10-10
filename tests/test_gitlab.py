import urllib.error

import pytest

from fake_gitlab import branch, not_found, repository
from kicad_bundle import gitlab

LINE = [("c3", "2026-09-23T17:12:23.000-07:00"), ("c2", "2026-08-29T16:00:00.000+01:00"),
        ("c1", "2026-08-01T09:00:00.000Z")]


def fetch_from(responses: dict[str, bytes]):
    def fetch(url: str) -> bytes:
        if url in responses:
            return responses[url]
        if url.startswith(f"{repository('kicad/code/wxWidgets')}/branches/"):
            raise not_found(url)
        raise AssertionError(url)  # GitLab answers anonymous commit listings with a Cloudflare challenge (403)
    return fetch


def test_head_at_is_the_branch_head_when_it_was_committed_by_then():
    fetch = fetch_from(branch("kicad/code/wxWidgets", "kicad/macos-wx-3.2", LINE))
    assert gitlab.head_at("kicad/code/wxWidgets", "kicad/macos-wx-3.2", "2026-10-01T00:00:00Z", fetch) == "c3"


def test_head_at_walks_first_parents_back_to_the_newest_commit_by_then_across_time_zones():
    fetch = fetch_from(branch("kicad/code/wxWidgets", "kicad/macos-wx-3.2", LINE))
    # c2 is 15:00Z, so a release at 15:43Z had it; c3 is 2026-09-24T00:12Z, after
    assert gitlab.head_at("kicad/code/wxWidgets", "kicad/macos-wx-3.2", "2026-08-29T15:43:28Z", fetch) == "c2"
    assert gitlab.head_at("kicad/code/wxWidgets", "kicad/macos-wx-3.2", "2026-08-29T14:59:59Z", fetch) == "c1"


def test_head_at_is_none_before_the_branchs_first_commit_or_without_the_branch():
    fetch = fetch_from(branch("kicad/code/wxWidgets", "kicad/macos-wx-3.2", LINE))
    assert gitlab.head_at("kicad/code/wxWidgets", "kicad/macos-wx-3.2", "2026-07-01T00:00:00Z", fetch) is None
    assert gitlab.head_at("kicad/code/wxWidgets", "11.0", "2026-10-01T00:00:00Z", fetch) is None


def test_head_at_passes_on_other_http_errors():
    def fetch(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)

    with pytest.raises(urllib.error.HTTPError, match="403"):
        gitlab.head_at("kicad/code/wxWidgets", "master", "2026-10-01T00:00:00Z", fetch)
