"""GitLab's API as gitlab.head_at reads it, for fake fetches: a branch and its first-parent line."""

import io
import json
import urllib.error
import urllib.parse

API = "https://gitlab.com/api/v4/projects"


def repository(project: str) -> str:
    return f"{API}/{urllib.parse.quote(project, safe='')}/repository"


def branch(project: str, ref: str, line: list[tuple[str, str]]) -> dict[str, bytes]:
    """The responses for ref, whose first-parent line is line: (commit id, committed date), newest first."""
    commits = [{"id": sha, "committed_date": date, "parent_ids": [line[i + 1][0]] if i + 1 < len(line) else []}
               for i, (sha, date) in enumerate(line)]
    repo = repository(project)
    return {f"{repo}/branches/{urllib.parse.quote(ref, safe='')}": json.dumps({"name": ref, "commit": commits[0]}).encode(),
            **{f"{repo}/commits/{c['id']}": json.dumps(c).encode() for c in commits}}


def not_found(url: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b'{"message":"404 Branch Not Found"}'))
