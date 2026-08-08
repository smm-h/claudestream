"""Detection of machine-local data, shared by the two hygiene test modules.

claudestream is a public repository that also ships its ``tests/`` and
``scripts/`` directories inside the published sdist. Anything written on a
developer's machine and committed here is published: recorded cassettes
(``tests/test_cassette_hygiene.py``) and source files alike
(``tests/test_repo_hygiene.py``).

Both need the same question answered -- "does this text carry a real path from
the machine that wrote it?" -- so the shapes live here once. The patterns match
*shapes*, not a list of known keys or known files, because the leak this module
exists for was a protocol key nobody had thought to scrub.
"""

from __future__ import annotations

import getpass
import re

#: Path segments that are obviously stand-ins rather than a real account name.
#: A path built from one of these is documentation or a test fixture
#: (``/home/user/project``), not a machine's identity. Kept deliberately short:
#: it is a list of synthetic words, not a per-file escape hatch.
SYNTHETIC_SEGMENTS = frozenset(
    {
        "someone",
        "test",
        "user",
        "username",
        "workspace",
        "you",
    }
)


def leaky_usernames() -> set[str]:
    """Account names whose appearance as a path segment is a leak.

    ``m`` is the historical leak these tests were written for. The current
    user is included so a re-record or a fresh script on any other machine is
    checked the same way.
    """
    names = {"m"}
    try:
        names.add(getpass.getuser())
    except Exception:  # noqa: BLE001 -- no login name available (some CI images)
        pass
    return {name for name in names if name and name not in SYNTHETIC_SEGMENTS}


#: URLs are stripped before any path scan: ``https://example.com/a/b`` has a
#: ``/``-separated tail that is not a filesystem path.
URL = re.compile(r"https?://[^\s\"'`)]+")

#: A home or temp directory *with a named thing under it*. Two refinements
#: keep this from firing on prose: the trailing separator distinguishes a real
#: path from a bare mention of the directory itself (which is how these very
#: patterns have to be written down), and the captured segment must contain a
#: word character, so an elided path written as an ellipsis is not a leak.
HOME_ROOTED_PATH = re.compile(
    r"(?:/home|/Users|/tmp|/var/folders)/([\w.+-]*\w[\w.+-]*)/"
)

#: A POSIX absolute path with at least two segments. One segment is not enough
#: to be a machine path: ``/workspace`` is the recorder's cwd placeholder and
#: ``/login`` is a slash command the CLI names in its output.
ABSOLUTE_PATH = re.compile(r"(?<![\w:])/[\w.+-]+/[\w.+-]+")


def strip_urls(text: str) -> str:
    """Remove URLs so their path tails do not read as filesystem paths."""
    return URL.sub("", text)


def find_machine_local_path(text: str) -> re.Match | None:
    """Return the first home/temp-rooted path with a non-synthetic segment.

    ``/home/user/project`` is a fixture and passes. ``/home/alice/project`` and
    ``/tmp/claude-1000/-home-alice-Projects/...`` do not.
    """
    for match in HOME_ROOTED_PATH.finditer(strip_urls(text)):
        if match.group(1) not in SYNTHETIC_SEGMENTS:
            return match
    return None


def find_username_segment(text: str) -> tuple[str, re.Match] | None:
    """Return the first account name appearing as a path segment, if any.

    Both separators matter. Claude Code flattens a project directory into its
    config tree by replacing separators with hyphens, so a home path can arrive
    hyphen-delimited rather than slash-delimited.
    """
    for name in sorted(leaky_usernames()):
        match = re.search(rf"[/\-]{re.escape(name)}[/\-]", text)
        if match is not None:
            return name, match
    return None


def excerpt(text: str, match: re.Match, width: int = 60) -> str:
    """A readable window around *match*, for assertion messages."""
    return repr(text[max(0, match.start() - width) : match.end() + width])
