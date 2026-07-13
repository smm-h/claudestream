"""Stage 4 observability tests: ToolResult.tool_name enrichment and Result.model_usage.

Item 12 exercises the real session-level enrichment path in _read_turn (the
tool_use_id -> name correlation), plus Result.model_usage parsing. The sync-path
check (relevant part of item 13) confirms the SyncSession consumer bridge
delivers the enriched fields intact.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from claudestream._async_session import AsyncSession
from claudestream._sync_session import SyncSession
from claudestream._options import SessionConfig
from claudestream.events import Result, ToolResult, ToolUse

from tests.conftest import make_test_session


def _build_ndjson(events: list[dict]) -> bytes:
    """Encode a list of raw event dicts as NDJSON bytes."""
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _prepare_session(session: AsyncSession, data: bytes) -> None:
    """Mock the process manager internals so _read_turn can read from data."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader
    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


def _tool_use_raw(tool_use_id: str, name: str) -> dict:
    return {
        "type": "assistant",
        "session_id": "s1",
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": name, "input": {"x": 1}}
            ],
            "model": "m",
            "stop_reason": "tool_use",
        },
    }


def _tool_result_raw(tool_use_id: str, content: str) -> dict:
    return {
        "type": "user",
        "session_id": "s1",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
            ]
        },
    }


_RESULT_RAW = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "done",
    "session_id": "s1",
}


async def _run_turn(data: bytes) -> tuple[AsyncSession, list]:
    """Prepare a fresh session inside the running loop and collect one turn.

    The StreamReader must be constructed while an event loop is running, so
    preparation happens inside the coroutine (mirrors test_cancellation).
    """
    session = make_test_session()
    _prepare_session(session, data)
    events = []
    async for event in session._read_turn(raw=False):
        events.append(event)
    return session, events


class TestToolNameEnrichment:
    """Item 12: a ToolResult is stamped with the name of the matching ToolUse."""

    def test_tool_result_enriched_from_prior_tool_use(self):
        data = _build_ndjson([
            _tool_use_raw("toolu_1", "Bash"),
            _tool_result_raw("toolu_1", "file.txt"),
            _RESULT_RAW,
        ])
        _session, events = asyncio.run(_run_turn(data))

        tool_uses = [e for e in events if isinstance(e, ToolUse)]
        tool_results = [e for e in events if isinstance(e, ToolResult)]
        assert len(tool_uses) == 1
        assert len(tool_results) == 1
        assert tool_uses[0].name == "Bash"
        assert tool_results[0].tool_use_id == "toolu_1"
        assert tool_results[0].tool_name == "Bash"

    def test_multiple_tools_map_independently(self):
        data = _build_ndjson([
            _tool_use_raw("toolu_a", "Read"),
            _tool_use_raw("toolu_b", "Edit"),
            _tool_result_raw("toolu_b", "edited"),
            _tool_result_raw("toolu_a", "contents"),
            _RESULT_RAW,
        ])
        _session, events = asyncio.run(_run_turn(data))

        by_id = {e.tool_use_id: e for e in events if isinstance(e, ToolResult)}
        assert by_id["toolu_a"].tool_name == "Read"
        assert by_id["toolu_b"].tool_name == "Edit"

    def test_unmatched_tool_result_keeps_none(self):
        # A ToolResult with no preceding ToolUse for its id must stay None.
        data = _build_ndjson([
            _tool_result_raw("toolu_orphan", "stuff"),
            _RESULT_RAW,
        ])
        _session, events = asyncio.run(_run_turn(data))

        tool_results = [e for e in events if isinstance(e, ToolResult)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_name is None

    def test_map_persists_across_turns(self):
        # The tool_use_id -> name map is per-session; a ToolResult arriving in a
        # later turn than its ToolUse is still enriched.
        async def run():
            session = make_test_session()
            _prepare_session(session, _build_ndjson([
                _tool_use_raw("toolu_x", "Grep"),
                _RESULT_RAW,
            ]))
            first = [e async for e in session._read_turn(raw=False)]
            _prepare_session(session, _build_ndjson([
                _tool_result_raw("toolu_x", "match"),
                _RESULT_RAW,
            ]))
            second = [e async for e in session._read_turn(raw=False)]
            return first, second

        _first, second = asyncio.run(run())
        tool_results = [e for e in second if isinstance(e, ToolResult)]
        assert len(tool_results) == 1
        assert tool_results[0].tool_name == "Grep"


class TestResultModelUsage:
    """Item 12: Result.model_usage surfaces through the session read path."""

    def test_model_usage_surfaced_in_read_turn(self):
        model_usage = {
            "claude-sonnet-4-5": {
                "inputTokens": 500,
                "outputTokens": 120,
                "contextWindow": 200000,
                "costUSD": 0.004,
            }
        }
        result_raw = dict(_RESULT_RAW)
        result_raw["modelUsage"] = model_usage
        data = _build_ndjson([_tool_use_raw("toolu_1", "Bash"), result_raw])

        _session, events = asyncio.run(_run_turn(data))

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].model_usage == model_usage


class TestSyncConsumerDeliversNewFields:
    """Relevant part of item 13: the sync consumer path delivers the enriched
    ToolResult and Result.model_usage without dropping the new fields."""

    def test_sync_send_yields_enriched_fields(self):
        enriched = ToolResult(
            type="tool_result", tool_use_id="toolu_1", content="ok", tool_name="Bash"
        )
        result = Result(
            type="result",
            subtype="success",
            result="done",
            model_usage={"m": {"inputTokens": 10}},
        )

        class _FakeAsync:
            def __init__(self):
                self.session_id = "id"
                self.model_name = "m"
                self.tools = []
                self.claude_version = "0.0.0"
                self.last_result = None

            async def _start(self):
                pass

            async def close(self):
                pass

            async def send(self, prompt, *, raw=False):
                yield enriched
                yield result

        session = SyncSession(SessionConfig(model="test", profile="test"))
        session._async_session = _FakeAsync()
        session._started = True
        session._ensure_loop()
        try:
            events = list(session.send("hi"))
        finally:
            session.close()

        tool_results = [e for e in events if isinstance(e, ToolResult)]
        results = [e for e in events if isinstance(e, Result)]
        assert tool_results[0].tool_name == "Bash"
        assert results[0].model_usage == {"m": {"inputTokens": 10}}
