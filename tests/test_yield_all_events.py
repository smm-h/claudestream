"""Tests that all events are yielded to consumers, including SystemInit and handled PermissionRequests."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from claudestream._async_session import AsyncSession
from claudestream.events import (
    AssistantText,
    PermissionRequest,
    Result,
    SystemInit,
)
from claudestream.policy import Sandbox

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
    # stdin needs drain() to be async (write_message awaits it)
    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


# -- Shared fixtures ---------------------------------------------------------

SYSTEM_INIT_RAW = {
    "type": "system",
    "subtype": "init",
    "cwd": "/home/test",
    "tools": ["Bash", "Read"],
    "mcp_servers": [],
    "model": "claude-sonnet-4-5",
    "permission_mode": "default",
    "claude_code_version": "2.1.128",
    "session_id": "test-session-123",
    "uuid": "uuid-init",
}

RESULT_RAW = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 1000.0,
    "duration_api_ms": 900.0,
    "num_turns": 1,
    "result": "Done.",
    "stop_reason": "end_turn",
    "total_cost_usd": 0.01,
    "session_id": "test-session-123",
    "uuid": "uuid-result",
}

PERMISSION_REQUEST_RAW = {
    "type": "sdk_control_request",
    "request": {
        "subtype": "permission",
        "request_id": "perm_1",
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "decision_reason": "not in allowlist",
        "tool_use_id": "tool_1",
    },
    "session_id": "test-session-123",
}


# -- Tests -------------------------------------------------------------------


class TestSystemInitYielded:
    """SystemInit events should be yielded to consumers (not swallowed)."""

    def test_system_init_in_event_stream(self):
        """SystemInit should appear in the consumer's event stream (raw=False)."""
        data = _build_ndjson([SYSTEM_INIT_RAW, RESULT_RAW])

        async def run():
            session = make_test_session()
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        events = asyncio.run(run())
        types = [type(e).__name__ for e in events]
        assert "SystemInit" in types
        # SystemInit should come before Result
        assert types.index("SystemInit") < types.index("Result")

    def test_system_init_metadata_still_captured(self):
        """Session metadata (session_id, model_name, tools) should still be
        populated from SystemInit even though it is now also yielded."""
        data = _build_ndjson([SYSTEM_INIT_RAW, RESULT_RAW])

        async def run():
            session = make_test_session()
            assert session.session_id is None
            assert session.model_name is None
            assert session.tools == []

            _prepare_session(session, data)
            async for _ in session._read_turn(raw=False):
                pass
            return session

        session = asyncio.run(run())
        assert session.session_id == "test-session-123"
        assert session.model_name == "claude-sonnet-4-5"
        assert session.tools == ["Bash", "Read"]


class TestPermissionRequestYieldedWhenHandled:
    """PermissionRequest events should be yielded even when the sandbox auto-handles them."""

    def test_handled_permission_request_yielded(self):
        """When the sandbox auto-allows a PermissionRequest, the event should still
        appear in the consumer's event stream."""
        data = _build_ndjson([PERMISSION_REQUEST_RAW, RESULT_RAW])

        async def run():
            session = make_test_session(sandbox=Sandbox(skip_permissions=True))
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())
        types = [type(e).__name__ for e in events]
        assert "PermissionRequest" in types

    def test_handled_permission_request_still_auto_responded(self):
        """The sandbox should still send the auto-response even though the event
        is also yielded to the consumer."""
        data = _build_ndjson([PERMISSION_REQUEST_RAW, RESULT_RAW])

        async def run():
            session = make_test_session(sandbox=Sandbox(skip_permissions=True))
            _prepare_session(session, data)

            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)

            # Check what was written to stdin (the auto-response)
            stdin = session._process_mgr._process.stdin
            return stdin, events

        stdin, events = asyncio.run(run())

        # The auto-response should have been written to stdin
        assert stdin.write.called
        # Decode the written NDJSON to verify it's an AllowPermission
        written_data = stdin.write.call_args_list[0][0][0]
        written_obj = json.loads(written_data.decode("utf-8"))
        assert written_obj["type"] == "control_response"
        response = written_obj["response"]
        assert response["request_id"] == "perm_1"
        assert response["response"]["behavior"] == "allow"

        # AND the PermissionRequest should also be in the yielded events
        perm_events = [e for e in events if isinstance(e, PermissionRequest)]
        assert len(perm_events) == 1
        assert perm_events[0].tool_name == "Bash"
