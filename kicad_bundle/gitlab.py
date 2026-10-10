"""GitLab's API, for KiCad's builders (kicad-mac-builder, kicad-win-builder) and other gitlab.com projects:
a branch as it was at a moment, and a file at a commit."""

import json
import urllib.error
import urllib.parse
from datetime import datetime

from kicad_bundle.fetch import Fetch

API = "https://gitlab.com/api/v4/projects"


def _repository(project: str) -> str:
    return f"{API}/{urllib.parse.quote(project, safe='')}/repository"


def head_at(project: str, ref: str, until: str, fetch: Fetch) -> str | None:
    """The commit ref pointed at by until (ISO 8601): the newest on its first-parent line committed by then,
    or None if it had none (or the branch doesn't exist). Walks back one commit at a time, because gitlab.com
    answers anonymous commit listings (`commits?until=`) with a Cloudflare challenge (simee-kicad#24)."""
    try:
        commit = json.loads(fetch(f"{_repository(project)}/branches/{urllib.parse.quote(ref, safe='')}"))["commit"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    moment = datetime.fromisoformat(until)
    while datetime.fromisoformat(commit["committed_date"]) > moment:
        if not commit["parent_ids"]:
            return None
        commit = json.loads(fetch(f"{_repository(project)}/commits/{commit['parent_ids'][0]}"))
    return commit["id"]


def file_at(project: str, path: str, commit: str, fetch: Fetch) -> str:
    return fetch(f"{_repository(project)}/files/{urllib.parse.quote(path, safe='')}/raw?ref={commit}").decode()
