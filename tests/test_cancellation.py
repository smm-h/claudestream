"""Tests for cancel() on AsyncSession and SyncSession."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream._sync_session import SyncSession
from claudestream.events import AssistantText


def _build_ndjson(events: list[dict]) -> bytes:
    """Encode a list of raw event dicts as NDJSON bytes."""
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _make_session() -> AsyncSession:
    """Create an AsyncSession with a mocked ProcessManager (no real subprocess)."""
    with patch("claudestream._async_session.find_binary", return_value="/fake/claude"), \
         patch("claudestream._async_session.check_version", new_callable=AsyncMock, return_value="2.1.0"), \
         patch("claudewheel.profile.resolve_profile", return_value={}):
        session = AsyncSession(model="haiku", profile="test", binary="/fake/claude")
    return session


def _prepare_session(session: AsyncSession, data: bytes) -> None:
    """Mock the process manager internals so _read_turn can read from data."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader
    session._process_mgr._process.stdin = MagicMock()


# A normal event stream with an assistant message + result
_NORMAL_EVENTS = [
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


class TestAsyncSessionGracefulCancel:
    def test_cancelled_flag_raises_in_read_turn(self):
        """Setting _cancelled before reading events raises ClaudeStreamError."""
        data = _build_ndjson(_NORMAL_EVENTS)

        async def run():
            session = _make_session()
            _prepare_session(session, data)
            session._cancelled = True
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return events

        with pytest.raises(ClaudeStreamError, match="Session cancelled"):
            asyncio.run(run())

    def test_graceful_cancel_closes_stdin(self):
        """cancel(force=False) sets flag and closes stdin."""
        async def run():
            session = _make_session()
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None
            mock_stdin = MagicMock()
            session._process_mgr._process.stdin = mock_stdin

            await session.cancel(force=False)

            assert session._cancelled is True
            mock_stdin.close.assert_called_once()

        asyncio.run(run())

    def test_force_cancel_calls_process_mgr_close(self):
        """cancel(force=True) sets flag and calls ProcessManager.close()."""
        async def run():
            session = _make_session()
            session._process_mgr.close = AsyncMock()

            await session.cancel(force=True)

            assert session._cancelled is True
            session._process_mgr.close.assert_awaited_once()

        asyncio.run(run())


class TestAsyncSessionCancelledReuse:
    def test_send_resets_cancelled_flag(self):
        """send() resets _cancelled so a cancelled session can be reused."""
        data = _build_ndjson(_NORMAL_EVENTS)

        async def run():
            session = _make_session()
            _prepare_session(session, data)
            session._cancelled = True

            # Set up stdin mock for write_message
            mock_stdin = MagicMock()
            mock_stdin.write = MagicMock()
            mock_stdin.drain = AsyncMock()
            session._process_mgr._process.stdin = mock_stdin

            # send() should reset _cancelled to False
            events = []
            async for event in session.send("hello"):
                events.append(event)

            # Session should have completed normally
            assert session._cancelled is False
            types = [type(e).__name__ for e in events]
            assert "AssistantText" in types
            assert "Result" in types

        asyncio.run(run())


class TestSyncSessionCancel:
    def test_cancel_delegates_to_async_session(self):
        """SyncSession.cancel() delegates to AsyncSession.cancel()."""
        fake_async = MagicMock()
        fake_async.cancel = AsyncMock()

        session = SyncSession(model="test", profile="test")
        loop = session._ensure_loop()
        session._async_session = fake_async

        session.cancel(force=False)
        fake_async.cancel.assert_awaited_once_with(force=False)

        fake_async.cancel.reset_mock()
        session.cancel(force=True)
        fake_async.cancel.assert_awaited_once_with(force=True)

        # Cleanup
        loop.call_soon_threadsafe(loop.stop)
        if session._thread:
            session._thread.join(timeout=2.0)

    def test_cancel_noop_without_async_session(self):
        """SyncSession.cancel() is a no-op if async session is None."""
        session = SyncSession(model="test", profile="test")
        # Should not raise
        session.cancel(force=False)
        session.cancel(force=True)
