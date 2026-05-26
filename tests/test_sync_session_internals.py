"""Tests for SyncSession internals: event-based startup, queue timeout, sequential sends."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._sync_session import SyncSession, _SENTINEL
from claudestream._options import SessionConfig
from claudestream.events import AssistantText


class _FakeAsyncSession:
    """Minimal async session stand-in for unit tests."""

    def __init__(self, events=None, error=None):
        self._events = events or []
        self._error = error
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
        if self._error is not None:
            raise self._error
        for ev in self._events:
            yield ev


def _make_session(**overrides):
    """Create a SyncSession with default kwargs, applying overrides."""
    kwargs = {"model": "test", "profile": "test"}
    kwargs.update(overrides)
    return SyncSession(SessionConfig(**kwargs))


class TestEventBasedStartup:
    """Issue 3: threading.Event replaces busy-wait spin loop."""

    def test_loop_ready_after_enter(self):
        fake = _FakeAsyncSession()
        session = _make_session()

        with patch.object(
            session, "_config", session._config
        ):
            # Manually wire up: start loop, inject fake async session
            session._async_session = fake
            loop = session._ensure_loop()
            session._started = True

            assert loop is not None
            assert session._loop is not None
            assert session._loop_ready.is_set()

            # Cleanup
            session.close()

    def test_loop_ready_event_is_set_before_ensure_returns(self):
        session = _make_session()
        loop = session._ensure_loop()
        # By the time _ensure_loop returns, the event must be set
        assert session._loop_ready.is_set()
        assert loop is session._loop
        # Cleanup
        loop.call_soon_threadsafe(loop.stop)
        session._thread.join(timeout=2.0)


class TestQueueTimeout:
    """Issue 2: queue.get(timeout=1.0) prevents infinite blocking."""

    def test_send_propagates_async_error(self):
        """If the async session raises, send() must propagate the error, not block."""
        error = RuntimeError("async boom")
        fake = _FakeAsyncSession(error=error)
        session = _make_session()
        session._async_session = fake
        session._started = True
        loop = session._ensure_loop()

        with pytest.raises(RuntimeError, match="async boom"):
            list(session.send("hello"))

        session.close()

    def test_send_propagates_error_from_drain(self):
        """Error put on queue by _drain's except clause is re-raised."""
        error = ValueError("drain error")
        fake = _FakeAsyncSession(error=error)
        session = _make_session()
        session._async_session = fake
        session._started = True
        session._ensure_loop()

        with pytest.raises(ValueError, match="drain error"):
            list(session.send("hello"))

        session.close()


class TestSequentialSends:
    """Verify two sequential send() calls work without deadlocking."""

    def test_two_sequential_sends(self):
        ev1 = AssistantText(type="assistant", text="one")
        ev2 = AssistantText(type="assistant", text="two")

        fake = _FakeAsyncSession(events=[ev1])
        session = _make_session()
        session._async_session = fake
        session._started = True
        session._ensure_loop()

        result1 = list(session.send("first"))
        assert len(result1) == 1
        assert result1[0].text == "one"

        # Swap events for second call
        fake._events = [ev2]
        result2 = list(session.send("second"))
        assert len(result2) == 1
        assert result2[0].text == "two"

        session.close()
