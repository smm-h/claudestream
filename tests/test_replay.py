"""Recorded-replay (VCR) protocol tests: the real stack, zero spend.

Every test here drives claudestream through a real subprocess -- real
``asyncio.create_subprocess_exec``, real pipe buffering, real NDJSON on stdin,
real bidirectional control round-trips -- against a cassette recorded from the
real Claude Code CLI. The in-process ``StreamReader`` doubles used elsewhere in
the suite cannot reach any of that.

The cassettes under ``tests/fixtures/transcripts/`` were captured by
``scripts/record_transcripts.py``; see that script for the re-record procedure
and for which scenarios cost money.

Nothing here needs credentials: the binary is redirected via
``SessionConfig.binary`` and the profile via claudewheel's own
``CLAUDEWHEEL_CONFIG_DIR`` workspace knob, so ``resolve_profile`` executes for
real against a throwaway workspace.
"""

from __future__ import annotations

import asyncio

import pytest

from claudestream import AsyncSession, ClaudeStreamError, Result, SystemInit, tool
from tests.vcr import (
    WORKSPACE_ENV,
    available_cassettes,
    cassette,
    fake_workspace,
    replay_env,
    replay_config,
)

#: Version string of the CLI the cassettes were recorded from. Replay serves it
#: through the same ``claude -v`` path production uses.
RECORDED_CLI_VERSION = "2.1.220"


@tool("test_server")
def greet(name: str) -> str:
    """Greet someone by name.

    Args:
        name: Name to greet.
    """
    return f"Hello, {name}!"


@pytest.fixture
def replay(tmp_path, monkeypatch):
    """Return a factory building ``SessionConfig``s bound to a named cassette.

    Two environments get pointed at the cassette, deliberately: the subprocess
    env (through ``SessionConfig.env``, which is what the session's own process
    receives) and this process's env, which the short-lived ``claude -v``
    version probe inherits. Both go through the stand-in binary, so the version
    check is exercised rather than bypassed.
    """
    workspace = tmp_path / "workspace"
    monkeypatch.setenv(WORKSPACE_ENV, str(workspace))
    profile = fake_workspace(workspace)

    def make(name: str, **kwargs):
        path = cassette(name)
        for key, value in replay_env(path).items():
            monkeypatch.setenv(key, value)
        return replay_config(path, profile=profile, **kwargs)

    return make


def test_the_recorded_cassettes_are_present():
    """The replay lane is only spend-free if its cassettes are committed."""
    assert "mcp_handshake" in available_cassettes()
    assert "simple_send" in available_cassettes()


def test_mcp_handshake_completes_over_a_real_subprocess(replay):
    """The four-round-trip startup handshake replays end to end.

    Reaching ``__aexit__`` without raising means every leg landed: the
    ``initialize`` control_request and its response, ``mcp_set_servers`` and
    its response, and the MCP JSON-RPC handshake (``initialize``,
    ``notifications/initialized``, ``tools/list``) with claudestream answering
    each ``mcp_message`` on stdin.
    """

    async def run():
        config = replay("mcp_handshake", tools=[greet._tool])
        async with AsyncSession(config) as session:
            assert session.claude_version == RECORDED_CLI_VERSION
            assert [t.name for t in session.user_tools] == ["greet"]

    asyncio.run(run())


def test_simple_send_decodes_system_init_and_result(replay):
    """A recorded turn decodes into typed events through the real pipe."""

    async def run():
        config = replay("simple_send")
        async with AsyncSession(config) as session:
            events = [
                event
                async for event in session.send(
                    "respond with exactly the word 'pong'", raw=True
                )
            ]

            inits = [e for e in events if isinstance(e, SystemInit)]
            assert len(inits) == 1
            assert inits[0].model == "claude-haiku-4-5-20251001"
            assert "Read" in inits[0].tools
            assert session.session_id == inits[0].session_id

            results = [e for e in events if isinstance(e, Result)]
            assert len(results) == 1
            # This cassette was recorded without credentials, which is exactly
            # why it was free: the CLI answers with an error result carrying a
            # zero cost. The decode path is the same one a paid result takes.
            assert results[0].is_error is True
            assert results[0].total_cost_usd == 0
            assert session.last_result is results[0]

    asyncio.run(run())


def test_replay_rejects_a_host_sequence_the_cassette_does_not_contain(replay):
    """A diverging host is a hard error, never a silently passing test.

    Replaying the handshake cassette from a session with no tools makes
    claudestream skip the ``initialize`` control_request the cassette expects
    first, so the stand-in binary refuses and the session fails.
    """

    async def run():
        config = replay("mcp_handshake")
        async with AsyncSession(config) as session:
            async for _ in session.send("anything", raw=True):
                pass

    with pytest.raises((ClaudeStreamError, asyncio.TimeoutError)):
        asyncio.run(run())
