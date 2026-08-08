"""No committed source file may carry a path from the machine that wrote it.

The sibling module ``test_cassette_hygiene.py`` guards recorded data. This one
guards the source, because the published sdist packages ``tests/`` and
``scripts/`` verbatim: a default output directory or a hardcoded binary path
written on a developer's machine is published exactly as typed.

Both defects this repository has actually had were of that shape -- a cassette
holding ``/tmp/.../-home-<user>-Projects-...`` and an integration test pinning
``/home/<user>/.local/bin/claude``. Neither was findable by reviewing the file
the reviewer happened to be looking at, so the check is repository-wide rather
than per-file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.hygiene import (
    SYNTHETIC_SEGMENTS,
    excerpt,
    find_machine_local_path,
    find_username_segment,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Suffixes worth scanning: text that a human or an agent writes and commits.
_TEXT_SUFFIXES = frozenset({".py", ".toml", ".json", ".md", ".sh", ".yml", ".yaml", ".cfg", ".txt"})

#: The hygiene helper spells the patterns out, so it necessarily contains the
#: words being searched for. Not a per-offender allowlist: one file, whose
#: entire job is to hold these strings.
_PATTERN_HOLDERS = frozenset({"tests/hygiene.py"})

#: ``todo/`` is exempt, and the reason is a policy conflict rather than a
#: judgement that its contents are safe. Todo files are immutable historical
#: artifacts -- they record what was understood when they were filed and are
#: never edited -- so a failure here could only be cleared by violating that
#: rule. One committed todo does mention a pytest temp path carrying the
#: author's account name. The remedy is packaging, not editing: ``todo/`` is
#: project management and has no business inside the published sdist. Until
#: that is decided, this exemption is the honest way to state the situation
#: instead of hiding it behind a green test.
_EXEMPT_PREFIXES = ("todo/",)


def _committed_text_files() -> list[Path]:
    """Every file git tracks that is worth scanning as text.

    Uses git rather than a glob so the scan follows what is actually published:
    untracked scratch files are not published and are not this test's business.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    ).stdout
    paths = []
    for name in listing.split("\0"):
        if not name or name in _PATTERN_HOLDERS:
            continue
        if name.startswith(_EXEMPT_PREFIXES):
            continue
        path = REPO_ROOT / name
        if path.suffix in _TEXT_SUFFIXES and path.is_file():
            paths.append(path)
    return sorted(paths)


_FILES = _committed_text_files()


def test_there_are_files_to_scan():
    """A vacuous pass would make every assertion below meaningless."""
    assert len(_FILES) > 20, f"only {len(_FILES)} committed text files found"


def test_no_committed_file_carries_a_machine_local_path():
    """Home and temp paths belong to whoever ran the tool, not to the repo.

    Synthetic segments are fine: ``/home/user/project`` is a fixture. A real
    account name is not. Replace the path with a required CLI argument, a
    repo-relative path, or ``tmp_path``.

    The scan reports every offender at once rather than one per test item: a
    leak of this kind tends to arrive in batches, and the whole list is what a
    fixer needs.
    """
    offenders = []
    for path in _FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        match = find_machine_local_path(text)
        if match is not None:
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}: {match.group()!r} "
                f"(segment {match.group(1)!r}) {excerpt(text, match)}"
            )
    assert not offenders, (
        "committed files carry machine-local paths (synthetic placeholders "
        f"{sorted(SYNTHETIC_SEGMENTS)} are allowed):\n" + "\n".join(offenders)
    )


def test_no_committed_file_carries_an_operator_username():
    """An account name as a path segment identifies the machine that wrote it."""
    offenders = []
    for path in _FILES:
        text = path.read_text(encoding="utf-8", errors="replace")
        found = find_username_segment(text)
        if found is not None:
            offenders.append(
                f"{path.relative_to(REPO_ROOT)}: username {found[0]!r} "
                f"{excerpt(text, found[1])}"
            )
    assert not offenders, (
        "committed files carry an account name as a path segment:\n"
        + "\n".join(offenders)
    )
