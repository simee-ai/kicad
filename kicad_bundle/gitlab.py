"""GitLab's API, for KiCad's builders (kicad-mac-builder, kicad-win-builder) and other gitlab.com projects:
a branch as it was at a moment, and a file at a commit."""

import json
import urllib.parse

from kicad_bundle.fetch import Fetch

API = "https://gitlab.com/api/v4/projects"


def _repository(project: str) -> str:
    return f"{API}/{urllib.parse.quote(project, safe='')}/repository"


def head_at(project: str, ref: str, until: str, fetch: Fetch) -> str | None:
    """The commit ref pointed at by until (ISO 8601), or None if it had none (or doesn't exist)."""
    query = urllib.parse.urlencode({"ref_name": ref, "until": until, "per_page": 1})
    commits = json.loads(fetch(f"{_repository(project)}/commits?{query}"))
    return commits[0]["id"] if commits else None


def file_at(project: str, path: str, commit: str, fetch: Fetch) -> str:
    return fetch(f"{_repository(project)}/files/{urllib.parse.quote(path, safe='')}/raw?ref={commit}").decode()
