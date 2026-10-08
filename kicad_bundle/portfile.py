"""The source downloads a vcpkg portfile makes, read without running it: just enough CMake (set, list,
string and the vcpkg fetch helpers) to resolve their URLs, file names, SHA512s and patches.

Control flow is ignored, so a download behind an if() is listed whatever the condition: the sources
of a port are then a superset of what the build used, never less. Every download is checked against
the SHA512 in the portfile, so a URL resolved wrongly fails rather than shipping the wrong source."""

import re
from dataclasses import dataclass

GITHUB = "https://github.com"
SOURCEFORGE = "https://sourceforge.net/projects"
UNKNOWN = "\0"  # marks the expansion of an undefined variable
_TOKEN = re.compile(r"""\s+|\#\[(=*)\[.*?\]\1\]|\#[^\n]*|\[(=*)\[\n?(.*?)\]\2\]|"((?:\\.|[^"\\])*)"|[^\s()#"]+|[()]""", re.S)
_CALL = re.compile(r"([A-Za-z_]\w*)\s*\(")
_VAR = re.compile(r"\$\{([^${}]*)\}")
_VAR_ONLY = re.compile(f"{UNKNOWN}[^{UNKNOWN}]*{UNKNOWN}")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", ";": ";"}


@dataclass(frozen=True)
class Download:
    urls: tuple[str, ...]  # tried in order
    filename: str
    sha512: str
    patches: tuple[str, ...]  # applied to it, in order (files of the port, or paths vcpkg made)
    main: bool  # the port's own source (SOURCE_PATH), where its licence files are


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), text)


def commands(text: str) -> list[tuple[str, list[tuple[str, bool]]]]:
    """Every command call in CMake source as (name, [(argument, quoted)]). Comments are dropped;
    bracket and quoted arguments count as quoted; parentheses nested in arguments are kept in them."""
    found, pos = [], 0
    while pos < len(text):
        if text[pos] == "#":
            pos = _TOKEN.match(text, pos).end()
            continue
        call = _CALL.match(text, pos)
        if not call:
            pos += 1
            continue
        args, depth, pos, current = [], 0, call.end(), None
        while True:
            token = _TOKEN.match(text, pos)
            pos = token.end()
            raw = token.group(0)
            if raw.isspace() or raw.startswith("#"):
                if current is not None:
                    args.append((current, False))
                    current = None
                continue
            if raw == ")" and depth == 0:
                break
            if token.group(3) is not None or token.group(4) is not None:
                args.append((token.group(3) if token.group(3) is not None else _unescape(token.group(4)), True))
                continue
            depth += raw.count("(") - raw.count(")")
            current = (current or "") + raw
        if current is not None:
            args.append((current, False))
        found.append((call.group(1), args))
    return found


class _Scope:
    def __init__(self, version: str):
        self.vars = {"VERSION": version}

    def expand(self, text: str) -> str:
        while m := _VAR.search(text):
            text = text[:m.start()] + self.vars.get(m.group(1), f"{UNKNOWN}{m.group(1)}{UNKNOWN}") + text[m.end():]
        return text

    def values(self, args: list[tuple[str, bool]]) -> list[str]:
        """Arguments after expansion; an unquoted one is a list and splits on ';'."""
        out = []
        for arg, quoted in args:
            value = self.expand(arg)
            # An undefined variable expands to nothing, so alone in an unquoted argument it's no item.
            out += [value] if quoted else [v for v in value.split(";") if v and not _VAR_ONLY.fullmatch(v)]
        return out


def _keywords(values: list[str], one: set[str], multi: set[str]) -> dict[str, str | list[str]]:
    """cmake_parse_arguments: one-value and multi-value keywords; anything else ends a multi-value run."""
    parsed, key = {}, None
    for v in values:
        if v in one or v in multi:
            key = v
            parsed[key] = [] if v in multi else ""
        elif key in one:
            parsed[key], key = v, None
        elif key in multi:
            parsed[key].append(v)
    return parsed


def _require(call: str, args: dict, *names: str) -> None:
    for name in names:
        value = args.get(name)
        if not value or UNKNOWN in "".join(value if isinstance(value, list) else [value]):
            shown = str(value).replace(UNKNOWN, "") if value else "nothing"
            raise RuntimeError(f"{call}: can't resolve {name} ({shown}) without running the portfile")


_FETCH_ONE = {"OUT_SOURCE_PATH", "REPO", "REF", "SHA512", "HEAD_REF", "GITHUB_HOST", "GITLAB_URL", "FILENAME",
              "FILE_DISAMBIGUATOR", "AUTHORIZATION_TOKEN", "WORKING_DIRECTORY"}


def _from_forge(name: str, values: list[str]) -> Download:
    a = _keywords(values, _FETCH_ONE, {"PATCHES"})
    _require(name, a, "REPO", "REF", "SHA512") if name != "vcpkg_from_sourceforge" else _require(name, a, "REPO", "SHA512", "FILENAME")
    repo, ref = a["REPO"], a.get("REF", "")
    sanitized = ref.replace("/", "_-")
    suffix = f"-{a['FILE_DISAMBIGUATOR']}" if a.get("FILE_DISAMBIGUATOR") else ""
    if name == "vcpkg_from_github":
        host = a.get("GITHUB_HOST", GITHUB).rstrip("/")
        url = (f"https://api.github.com/repos/{repo}/tarball/{ref}" if "USE_TARBALL_API" in values
               else f"{host}/{repo}/archive/{ref}.tar.gz")
        filename = f"{repo.replace('/', '-')}-{sanitized}{suffix}.tar.gz"
    elif name == "vcpkg_from_gitlab":
        _require(name, a, "GITLAB_URL")
        url = f"{a['GITLAB_URL'].rstrip('/')}/{repo}/-/archive/{ref}/{repo.rsplit('/', 1)[-1]}-{ref}.tar.gz"
        filename = f"{repo.replace('/', '-')}-{sanitized}{suffix}.tar.gz"
    else:
        org, _, project = repo.partition("/")
        url = (f"{SOURCEFORGE}/{org}/files/{project}/{ref}/{a['FILENAME']}/download" if ref
               else f"{SOURCEFORGE}/{repo}/files/{a['FILENAME']}/download")
        filename = a["FILENAME"]
    return Download((url,), filename, a["SHA512"].lower(), tuple(a.get("PATCHES", [])),
                    main=a.get("OUT_SOURCE_PATH") == "SOURCE_PATH")


FORGES = ("vcpkg_from_github", "vcpkg_from_gitlab", "vcpkg_from_sourceforge")


def downloads(portfile: str, version: str) -> list[Download]:
    """The source downloads of a portfile, for the port version given, in the order it makes them."""
    scope, found, nested = _Scope(version), [], 0
    for name, args in commands(portfile):
        lower = name.lower()
        if lower in ("function", "macro"):
            nested += 1
        elif lower in ("endfunction", "endmacro"):
            nested -= 1
        if nested or lower in ("endfunction", "endmacro"):
            continue
        values = scope.values(args)
        if lower == "set" and values:
            kept = values[1:values.index("CACHE")] if "CACHE" in values else [v for v in values[1:] if v != "PARENT_SCOPE"]
            if kept:
                scope.vars[values[0]] = ";".join(kept)
            else:
                scope.vars.pop(values[0], None)
        elif lower == "list" and len(values) >= 2 and values[0] in ("APPEND", "PREPEND"):
            old = [v for v in scope.vars.get(values[1], "").split(";") if v]
            new = values[2:] + old if values[0] == "PREPEND" else old + values[2:]
            scope.vars[values[1]] = ";".join(new)
        elif lower == "string" and values:
            _string(scope, values)
        elif lower in FORGES:
            found.append(_from_forge(lower, values))
        elif lower == "vcpkg_download_distfile":
            a = _keywords(values[1:], _FETCH_ONE, {"URLS", "HEADERS"})
            _require(name, a, "URLS", "FILENAME", "SHA512")
            found.append(Download(tuple(a["URLS"]), a["FILENAME"], a["SHA512"].lower(), (), main=False))
            scope.vars[values[0]] = f"{UNKNOWN}download:{len(found) - 1}{UNKNOWN}"
        elif lower in ("vcpkg_extract_source_archive", "vcpkg_extract_source_archive_ex"):
            a = _keywords(values, {"OUT_SOURCE_PATH", "ARCHIVE", "SOURCE_BASE", "BASE_DIRECTORY",
                                   "WORKING_DIRECTORY", "REF"}, {"PATCHES"})
            if m := re.fullmatch(f"{UNKNOWN}download:(\\d+){UNKNOWN}", a.get("ARCHIVE", "")):
                i = int(m.group(1))
                out = a.get("OUT_SOURCE_PATH") or (values[0] if values[0] != "ARCHIVE" else "")
                d = found[i]
                found[i] = Download(d.urls, d.filename, d.sha512, tuple(a.get("PATCHES", [])), out == "SOURCE_PATH")
        elif lower.startswith("vcpkg_from_"):
            raise RuntimeError(f"{name}: this kind of source download isn't supported yet")
    return found


def _string(scope: _Scope, values: list[str]) -> None:
    mode = values[0]
    if mode == "REPLACE" and len(values) >= 4:
        scope.vars[values[3]] = "".join(values[4:]).replace(values[1], values[2])
    elif mode == "REGEX" and len(values) >= 5 and values[1] in ("MATCH", "REPLACE"):
        if values[1] == "MATCH":
            pattern, out, text = values[2], values[3], "".join(values[4:])
            m = re.search(pattern, text)
            scope.vars[out] = m.group(0) if m else ""
            for i in range(10):
                scope.vars[f"CMAKE_MATCH_{i}"] = (m.group(i) or "") if m and i <= m.re.groups else ""
        elif len(values) >= 6:
            pattern, repl, out, text = values[2], values[3], values[4], "".join(values[5:])
            scope.vars[out] = re.sub(pattern, repl, text)
    elif mode in ("TOLOWER", "TOUPPER") and len(values) == 3:
        scope.vars[values[2]] = values[1].lower() if mode == "TOLOWER" else values[1].upper()
