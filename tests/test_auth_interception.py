"""Tests for authentication error interception in _read_turn."""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream.events import AssistantText, Result

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
    session._process_mgr._process.stdin = MagicMock()


class TestAuthInterception:
    def test_error_field_not_logged_in(self):
        """AssistantMessage with error='Not logged in' raises ClaudeStreamError."""
        raw_events = [
            {
                "type": "assistant",
                "session_id": "s1",
                "message": {
                    "content": [],
                    "model": "claude-sonnet-4-5",
                    "stop_reason": "end_turn",
                    "error": "Not logged in · Please run /login",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            },
        ]
        data = _build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        with pytest.raises(ClaudeStreamError, match="Authentication failed"):
            asyncio.run(run())

    def test_content_block_401(self):
        """AssistantMessage with TextBlock containing '401' raises ClaudeStreamError."""
        raw_events = [
            {
                "type": "assistant",
                "session_id": "s1",
                "error": None,
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Failed to authenticate. API Error: 401 Invalid authentication credentials.",
                        },
                    ],
                    "model": "claude-sonnet-4-5",
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            },
        ]
        data = _build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        with pytest.raises(ClaudeStreamError, match="Authentication failed"):
            asyncio.run(run())

    def test_normal_assistant_no_raise(self):
        """AssistantMessage with normal content does NOT raise."""
        raw_events = [
            {
                "type": "assistant",
                "session_id": "s1",
                "error": None,
                "message": {
                    "content": [{"type": "text", "text": "Hello, world!"}],
                    "model": "claude-sonnet-4-5",
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Hello, world!",
                "session_id": "s1",
            },
        ]
        data = _build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        events = asyncio.run(run())
        # Should have an AssistantText and a Result, no exception
        types = [type(e).__name__ for e in events]
        assert "AssistantText" in types
        assert "Result" in types

    def test_content_mentioning_401_no_false_positive(self):
        """AssistantMessage discussing HTTP 401 in conversation does NOT raise."""
        raw_events = [
            {
                "type": "assistant",
                "session_id": "s1",
                "error": None,
                "message": {
                    "content": [{"type": "text", "text": "HTTP 401 errors typically indicate that authentication credentials are missing or invalid."}],
                    "model": "claude-sonnet-4-5",
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            },
        ]
        data = _build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        events = asyncio.run(run())
        types = [type(e).__name__ for e in events]
        assert "AssistantText" in types
        assert "Result" in types

    def test_unrelated_error_no_raise(self):
        """AssistantMessage with an unrelated error field does NOT raise."""
        raw_events = [
            {
                "type": "assistant",
                "session_id": "s1",
                "message": {
                    "content": [{"type": "text", "text": "Sorry, something went wrong."}],
                    "model": "claude-sonnet-4-5",
                    "stop_reason": "end_turn",
                    "error": "Some other error occurred",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            },
        ]
        data = _build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        # Should NOT raise - the error is unrelated to auth
        events = asyncio.run(run())
        types = [type(e).__name__ for e in events]
        assert "AssistantText" in types
        assert "Result" in types
