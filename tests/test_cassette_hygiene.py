"""Committed cassettes must carry nothing machine-local.

The cassettes under ``tests/fixtures/transcripts/`` are recorded from a real
Claude Code CLI on a developer machine and then committed to a **public**
repository. The recorder scrubs the machine's inventory as it writes
(``tests/fixtures/claude_vcr.py``), but a scrubber is a whitelist of keys it
knows about, and the protocol grows keys. This module is the backstop that does
not need to know the schema: it greps the committed bytes for the *shapes* of
machine-local data.

It exists because the scrubber once missed one. ``memory_paths`` arrived as a
dict where the scrubber only emptied lists, so a real
``/tmp/.../-home-<user>-Projects-claudestream/...`` path shipped in
``simple_send.jsonl``. A key-shaped fix alone would leave the next such key
undefended; this test defends the class.
"""

from __future__ import annotations

import getpass
import importlib.util
import re

import pytest

from tests.vcr import TRANSCRIPTS_DIR, VCR_BINARY


def _load_vcr_module():
    """Import the stand-in binary as a module.

    ``tests/fixtures/claude_vcr.py`` is an executable script, not a package
    member, so it is loaded by path rather than by import statement.
    """
    spec = importlib.util.spec_from_file_location("claude_vcr", VCR_BINARY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vcr = _load_vcr_module()

#: Usernames whose appearance as a path segment means a recording machine's
#: identity leaked. ``m`` is the historical leak this test was written for; the
#: current user is added so a re-record on any machine is checked too.
def _leaky_usernames() -> set[str]:
    names = {"m"}
    try:
        names.add(getpass.getuser())
    except Exception:  # noqa: BLE001 -- no login name available (some CI images)
        pass
    return {name for name in names if name}


#: URLs are stripped before the absolute-path scan: ``https://claude.com/x``
#: contains a ``/``-separated tail that is not a filesystem path.
_URL = re.compile(r"https?://[^\s\"']+")

#: A POSIX absolute path with at least two segments (``/a/b``). One segment is
#: deliberately allowed: ``/workspace`` is the scrubber's own cwd placeholder,
#: and ``/login`` is a slash command the CLI names in its "not logged in"
#: result text. Two segments is the shape a real filesystem path takes.
_ABSOLUTE_PATH = re.compile(r"(?<![\w:])/[\w.+-]+/[\w.+-]+")

_FORBIDDEN = (
    ("a home directory", re.compile(r"/home/")),
    ("a temp directory", re.compile(r"/tmp/")),
    ("a HOME reference", re.compile(r"\$HOME|\$\{HOME\}|(?<![\w])~/")),
)


def _cassettes() -> list:
    return sorted(TRANSCRIPTS_DIR.glob("*.jsonl"))


def test_there_are_cassettes_to_check():
    """A vacuous pass would make every assertion below meaningless."""
    assert _cassettes(), f"no cassettes under {TRANSCRIPTS_DIR}"


@pytest.mark.parametrize("cassette", _cassettes(), ids=lambda path: path.name)
def test_cassette_carries_no_machine_local_paths(cassette):
    """No cassette line may contain a home dir, a temp dir or a $HOME reference."""
    text = cassette.read_text(encoding="utf-8")
    for label, pattern in _FORBIDDEN:
        match = pattern.search(text)
        assert match is None, (
            f"{cassette.name} contains {label} at offset {match.start()}: "
            f"{text[max(0, match.start() - 60):match.end() + 60]!r}"
        )


@pytest.mark.parametrize("cassette", _cassettes(), ids=lambda path: path.name)
def test_cassette_carries_no_operator_username(cassette):
    """The recording machine's login name may not appear as a path segment."""
    text = cassette.read_text(encoding="utf-8")
    for name in _leaky_usernames():
        # Both separators matter: `/home/m/x` and the flattened project-dir
        # form `-home-m-Projects-x` that Claude Code writes under its config.
        pattern = re.compile(rf"[/\-]{re.escape(name)}[/\-]")
        match = pattern.search(text)
        assert match is None, (
            f"{cassette.name} contains the username {name!r} as a path segment "
            f"at offset {match.start()}: "
            f"{text[max(0, match.start() - 60):match.end() + 60]!r}"
        )


@pytest.mark.parametrize("cassette", _cassettes(), ids=lambda path: path.name)
def test_cassette_carries_no_absolute_paths(cassette):
    """Nothing shaped like a real filesystem path survives into a cassette."""
    text = _URL.sub("", cassette.read_text(encoding="utf-8"))
    match = _ABSOLUTE_PATH.search(text)
    assert match is None, (
        f"{cassette.name} contains an absolute path {match.group()!r} at offset "
        f"{match.start()}: {text[max(0, match.start() - 60):match.end() + 60]!r}"
    )


class TestScrubber:
    """The recorder's own scrub step, pinned at the source of the leak."""

    def test_a_dict_valued_inventory_key_is_emptied(self):
        """The regression: ``memory_paths`` is a dict, and dicts were copied."""
        payload = {
            "type": "system",
            "memory_paths": {"auto": "/tmp/rec-abc/projects/-home-someone-x/memory/"},
        }
        assert vcr._scrub(payload)["memory_paths"] == {}

    def test_a_list_valued_inventory_key_is_emptied(self):
        assert vcr._scrub({"slash_commands": ["deploy", "standup"]})["slash_commands"] == []

    def test_a_string_valued_inventory_key_becomes_a_placeholder(self):
        """The key keeps a string type; the machine's content does not survive."""
        scrubbed = vcr._scrub({"memory_paths": "/home/someone/memory"})
        assert scrubbed["memory_paths"] == vcr._SCRUBBED_TEXT

    def test_every_inventory_key_is_emptied_whatever_its_type(self):
        """No key in the set may depend on the CLI's current choice of type."""
        for key in vcr._EMPTIED_INVENTORY_KEYS:
            assert vcr._scrub({key: {"leak": "/home/someone"}})[key] == {}
            assert vcr._scrub({key: ["/home/someone"]})[key] == []
            assert vcr._scrub({key: "/home/someone"})[key] == vcr._SCRUBBED_TEXT

    def test_the_tools_inventory_keeps_only_public_tools(self):
        """The operator's private tool surface does not ship in a cassette."""
        payload = {"tools": ["Read", "Bash", "DesignSync", "CronCreate", "Workflow"]}
        assert vcr._scrub(payload)["tools"] == ["Read", "Bash"]

    def test_mcp_tool_objects_survive_the_tools_filter(self):
        """``tools/list`` carries claudestream's own tools as objects, not names."""
        payload = {"result": {"tools": [{"name": "greet", "description": "hi"}]}}
        assert vcr._scrub(payload)["result"]["tools"] == [
            {"name": "greet", "description": "hi"}
        ]

    def test_cwd_and_pid_are_still_replaced(self):
        scrubbed = vcr._scrub({"cwd": "/home/someone/Projects/x", "pid": 4242})
        assert scrubbed == {"cwd": "/workspace", "pid": 0}

    def test_scrubbing_reaches_nested_payloads(self):
        payload = {"response": {"response": {"commands": [{"name": "deploy"}]}}}
        assert vcr._scrub(payload)["response"]["response"]["commands"] == []
