"""Tests for AskResult and ask() convenience method."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._async_session import AsyncSession
from claudestream._sync_session import SyncSession
from claudestream.events import AskResult, AssistantText, Result, Usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAsyncSession:
    """Minimal async session stand-in for unit tests."""

    def __init__(self, events=None):
        self._events = events or []
        self.session_id = "fake-id"
        self.model_name = "fake-model"
        self.tools = []
        self.claude_version = "0.0.0"
        self.last_result = None

    async def _start(self):
        pass

    async def close(self):
        pass

    async def send(self, prompt, *, raw=False):
        for ev in self._events:
            yield ev

    async def ask(self, prompt):
        parts: list[str] = []
        result_event: Result | None = None
        async for event in self.send(prompt):
            if isinstance(event, AssistantText):
                parts.append(event.text)
            elif isinstance(event, Result):
                result_event = event

        text = "".join(parts)
        if result_event:
            return AskResult(
                text=text,
                usage=result_event.usage,
                cost_usd=result_event.total_cost_usd,
                duration_ms=result_event.duration_ms,
                is_error=result_event.is_error,
            )
        return AskResult(text=text)


def _build_ndjson(events: list[dict]) -> bytes:
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _make_async_session() -> AsyncSession:
    with patch("claudestream._async_session.find_binary", return_value="/fake/claude"), \
         patch("claudestream._async_session.check_version", new_callable=AsyncMock, return_value="2.1.0"), \
         patch("claudewheel.profile.resolve_profile", return_value={}):
        session = AsyncSession(model="haiku", profile="test", binary="/fake/claude")
    return session


def _wire_ndjson(session: AsyncSession, data: bytes) -> None:
    """Wire NDJSON data into a mocked process for the session."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None

    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader

    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


# ---------------------------------------------------------------------------
# Raw event fixtures used by multiple tests
# ---------------------------------------------------------------------------

_SYSTEM_INIT = {
    "type": "system",
    "subtype": "init",
    "cwd": "/test",
    "tools": [],
    "model": "test-model",
    "session_id": "s1",
}

_ASSISTANT_HELLO = {
    "type": "assistant",
    "session_id": "s1",
    "message": {
        "content": [{"type": "text", "text": "Hello"}],
        "model": "test-model",
        "stop_reason": "end_turn",
    },
}

_ASSISTANT_WORLD = {
    "type": "assistant",
    "session_id": "s1",
    "message": {
        "content": [{"type": "text", "text": " world"}],
        "model": "test-model",
        "stop_reason": "end_turn",
    },
}

_RESULT_SUCCESS = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 1500.0,
    "duration_api_ms": 1200.0,
    "num_turns": 1,
    "result": "Hello world",
    "stop_reason": "end_turn",
    "total_cost_usd": 0.025,
    "session_id": "s1",
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
    },
}

_RESULT_ERROR = {
    "type": "result",
    "subtype": "error",
    "is_error": True,
    "duration_ms": 500.0,
    "duration_api_ms": 400.0,
    "num_turns": 1,
    "result": "",
    "stop_reason": "error",
    "total_cost_usd": 0.001,
    "session_id": "s1",
    "usage": {"input_tokens": 20, "output_tokens": 0},
}

_RESULT_BARE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "done",
    "session_id": "s1",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAskCollectsText:
    """ask() concatenates all AssistantText events into a single string."""

    def test_ask_collects_text(self):
        data = _build_ndjson([_SYSTEM_INIT, _ASSISTANT_HELLO, _ASSISTANT_WORLD, _RESULT_SUCCESS])

        async def run():
            session = _make_async_session()
            _wire_ndjson(session, data)
            return await session.ask("hi")

        result = asyncio.run(run())
        assert result.text == "Hello world"


class TestAskCapturesUsage:
    """ask() captures usage, cost, duration, and is_error from Result."""

    def test_ask_captures_metadata(self):
        data = _build_ndjson([_SYSTEM_INIT, _ASSISTANT_HELLO, _RESULT_SUCCESS])

        async def run():
            session = _make_async_session()
            _wire_ndjson(session, data)
            return await session.ask("hi")

        result = asyncio.run(run())
        assert result.usage is not None
        assert result.usage.input_tokens == 100
        assert result.usage.output_tokens == 50
        assert result.cost_usd == 0.025
        assert result.duration_ms == 1500.0
        assert result.is_error is False

    def test_ask_captures_error(self):
        data = _build_ndjson([_SYSTEM_INIT, _RESULT_ERROR])

        async def run():
            session = _make_async_session()
            _wire_ndjson(session, data)
            return await session.ask("hi")

        result = asyncio.run(run())
        assert result.is_error is True
        assert result.text == ""
        assert result.cost_usd == 0.001


class TestAskNoResult:
    """Edge case: no Result event yields AskResult with defaults."""

    def test_ask_no_result_event(self):
        """If somehow no Result event arrives (shouldn't happen but handle gracefully),
        we test via _FakeAsyncSession that yields only AssistantText."""
        events = [
            AssistantText(type="assistant", text="partial"),
        ]
        fake = _FakeAsyncSession(events=events)

        async def run():
            return await fake.ask("hi")

        result = asyncio.run(run())
        assert result.text == "partial"
        assert result.usage is None
        assert result.cost_usd == 0.0
        assert result.duration_ms == 0.0
        assert result.is_error is False


class TestAskEmptyResponse:
    """No AssistantText events, just Result -> empty text."""

    def test_ask_empty_text(self):
        data = _build_ndjson([_SYSTEM_INIT, _RESULT_BARE])

        async def run():
            session = _make_async_session()
            _wire_ndjson(session, data)
            return await session.ask("hi")

        result = asyncio.run(run())
        assert result.text == ""


class TestAskResultIsFrozen:
    """AskResult is immutable (frozen=True)."""

    def test_frozen(self):
        result = AskResult(text="hello")
        with pytest.raises(AttributeError):
            result.text = "changed"

    def test_frozen_cost(self):
        result = AskResult(text="hi", cost_usd=0.01)
        with pytest.raises(AttributeError):
            result.cost_usd = 999.0


class TestSyncAsk:
    """SyncSession.ask() delegates to AsyncSession.ask()."""

    def test_sync_ask(self):
        events = [
            AssistantText(type="assistant", text="sync "),
            AssistantText(type="assistant", text="response"),
            Result(
                type="result",
                is_error=False,
                duration_ms=200.0,
                total_cost_usd=0.005,
                usage=Usage(input_tokens=10, output_tokens=5),
            ),
        ]
        fake = _FakeAsyncSession(events=events)

        session = SyncSession(model="test", profile="test")
        session._async_session = fake
        session._started = True
        session._ensure_loop()

        result = session.ask("hello")
        assert result.text == "sync response"
        assert result.cost_usd == 0.005
        assert result.duration_ms == 200.0

        session.close()

    def test_sync_ask_not_started(self):
        session = SyncSession(model="test", profile="test")
        with pytest.raises(RuntimeError, match="Session not started"):
            session.ask("hi")


class TestPrintPromptUsesAsk:
    """print_prompt returns correct text (via ask())."""

    def test_print_prompt_returns_text(self):
        events = [
            AssistantText(type="assistant", text="prompt "),
            AssistantText(type="assistant", text="response"),
            Result(type="result", is_error=False, result="done"),
        ]
        fake = _FakeAsyncSession(events=events)

        with patch("claudestream.SyncSession") as MockSyncSession:
            mock_instance = MagicMock()
            mock_instance.__enter__ = MagicMock(return_value=mock_instance)
            mock_instance.__exit__ = MagicMock(return_value=False)
            mock_instance.ask.return_value = AskResult(text="prompt response")
            MockSyncSession.return_value = mock_instance

            from claudestream import print_prompt
            text = print_prompt("hi", model="test", profile="test")

            assert text == "prompt response"
            mock_instance.ask.assert_called_once_with("hi")
