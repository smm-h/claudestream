"""Tests for on_turn_complete, on_error, and on_close lifecycle hooks."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream._sync_session import SyncSession
from claudestream._options import SessionConfig
from claudestream.events import Result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(**kwargs) -> AsyncSession:
    from tests.conftest import make_test_session
    return make_test_session(**kwargs)


def _build_ndjson(events: list[dict]) -> bytes:
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _wire_ndjson(session: AsyncSession, data: bytes) -> None:
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None

    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader

    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


_SYSTEM_INIT = {
    "type": "system",
    "subtype": "init",
    "cwd": "/workspace",
    "tools": ["Read"],
    "mcp_servers": [],
    "model": "haiku",
    "session_id": "sid-1",
    "permission_mode": "allowedTools",
}

_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 50.0,
    "duration_api_ms": 40.0,
    "num_turns": 1,
    "result": "ok",
    "session_id": "sid-1",
    "total_cost_usd": 0.001,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}


# ---------------------------------------------------------------------------
# on_turn_complete
# ---------------------------------------------------------------------------


class TestOnTurnComplete:
    def test_fires_after_result(self):
        calls = []

        def hook(session, result):
            calls.append(("sync", session, result))

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT, _RESULT]))
            session.on_turn_complete(hook)
            async for _ in session.send("hi"):
                pass
            return session

        session = asyncio.run(run())
        assert len(calls) == 1
        assert calls[0][0] == "sync"
        assert calls[0][1] is session
        assert isinstance(calls[0][2], Result)

    def test_receives_session_and_result(self):
        received = {}

        def hook(session, result):
            received["session"] = session
            received["result"] = result

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT, _RESULT]))
            session.on_turn_complete(hook)
            async for _ in session.send("hi"):
                pass
            return session

        session = asyncio.run(run())
        assert received["session"] is session
        assert isinstance(received["result"], Result)
        assert received["result"].total_cost_usd == 0.001

    def test_multiple_hooks_fire_in_order(self):
        order = []

        def hook_a(session, result):
            order.append("a")

        def hook_b(session, result):
            order.append("b")

        def hook_c(session, result):
            order.append("c")

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT, _RESULT]))
            session.on_turn_complete(hook_a)
            session.on_turn_complete(hook_b)
            session.on_turn_complete(hook_c)
            async for _ in session.send("hi"):
                pass

        asyncio.run(run())
        assert order == ["a", "b", "c"]

    def test_hook_error_does_not_crash_session(self, caplog):
        calls = []

        def bad_hook(session, result):
            raise ValueError("boom")

        def good_hook(session, result):
            calls.append("good")

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT, _RESULT]))
            session.on_turn_complete(bad_hook)
            session.on_turn_complete(good_hook)
            async for _ in session.send("hi"):
                pass

        with caplog.at_level(logging.WARNING, logger="claudestream"):
            asyncio.run(run())

        # Good hook still ran despite bad hook raising
        assert calls == ["good"]
        assert "bad_hook" in caplog.text
        assert "boom" in caplog.text

    def test_async_hook_is_awaited(self):
        calls = []

        async def async_hook(session, result):
            await asyncio.sleep(0)
            calls.append("async")

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT, _RESULT]))
            session.on_turn_complete(async_hook)
            async for _ in session.send("hi"):
                pass

        asyncio.run(run())
        assert calls == ["async"]


# ---------------------------------------------------------------------------
# on_error
# ---------------------------------------------------------------------------


class TestOnError:
    def test_fires_on_exception(self):
        calls = []

        def hook(session, exc):
            calls.append(("error", type(exc).__name__, str(exc)))

        async def run():
            session = _make_session()
            # Wire ndjson with no Result event -- stdout closes, causing ClaudeStreamError
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT]))
            session.on_error(hook)
            with pytest.raises(ClaudeStreamError):
                async for _ in session.send("hi"):
                    pass

        asyncio.run(run())
        assert len(calls) == 1
        assert calls[0][0] == "error"
        assert calls[0][1] == "ClaudeStreamError"

    def test_error_hook_receives_session_and_exception(self):
        received = {}

        def hook(session, exc):
            received["session"] = session
            received["exc"] = exc

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT]))
            session.on_error(hook)
            with pytest.raises(ClaudeStreamError):
                async for _ in session.send("hi"):
                    pass
            return session

        session = asyncio.run(run())
        assert received["session"] is session
        assert isinstance(received["exc"], ClaudeStreamError)

    def test_error_hook_does_not_suppress_exception(self):
        def hook(session, exc):
            pass  # Just observe

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT]))
            session.on_error(hook)
            with pytest.raises(ClaudeStreamError):
                async for _ in session.send("hi"):
                    pass

        asyncio.run(run())

    def test_async_error_hook_is_awaited(self):
        calls = []

        async def async_hook(session, exc):
            await asyncio.sleep(0)
            calls.append("async_error")

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT]))
            session.on_error(async_hook)
            with pytest.raises(ClaudeStreamError):
                async for _ in session.send("hi"):
                    pass

        asyncio.run(run())
        assert calls == ["async_error"]

    def test_does_not_fire_on_success(self):
        calls = []

        def hook(session, exc):
            calls.append("should_not_fire")

        async def run():
            session = _make_session()
            _wire_ndjson(session, _build_ndjson([_SYSTEM_INIT, _RESULT]))
            session.on_error(hook)
            async for _ in session.send("hi"):
                pass

        asyncio.run(run())
        assert calls == []


# ---------------------------------------------------------------------------
# on_close
# ---------------------------------------------------------------------------


class TestOnClose:
    def test_fires_on_close(self):
        calls = []

        def hook(session):
            calls.append("closed")

        async def run():
            session = _make_session()
            session.on_close(hook)
            session._process_mgr.close = AsyncMock()
            await session.close()

        asyncio.run(run())
        assert calls == ["closed"]

    def test_receives_session(self):
        received = {}

        def hook(session):
            received["session"] = session

        async def run():
            session = _make_session()
            session.on_close(hook)
            session._process_mgr.close = AsyncMock()
            await session.close()
            return session

        session = asyncio.run(run())
        assert received["session"] is session

    def test_fires_before_process_close(self):
        order = []

        def close_hook(session):
            order.append("hook")

        async def mock_close():
            order.append("process_close")

        async def run():
            session = _make_session()
            session.on_close(close_hook)
            session._process_mgr.close = mock_close
            await session.close()

        asyncio.run(run())
        assert order == ["hook", "process_close"]

    def test_async_close_hook(self):
        calls = []

        async def async_hook(session):
            await asyncio.sleep(0)
            calls.append("async_close")

        async def run():
            session = _make_session()
            session.on_close(async_hook)
            session._process_mgr.close = AsyncMock()
            await session.close()

        asyncio.run(run())
        assert calls == ["async_close"]

    def test_close_hook_error_does_not_prevent_process_close(self, caplog):
        process_closed = []

        def bad_hook(session):
            raise RuntimeError("close boom")

        async def mock_close():
            process_closed.append(True)

        async def run():
            session = _make_session()
            session.on_close(bad_hook)
            session._process_mgr.close = mock_close
            await session.close()

        with caplog.at_level(logging.WARNING, logger="claudestream"):
            asyncio.run(run())

        assert process_closed == [True]
        assert "close boom" in caplog.text


# ---------------------------------------------------------------------------
# SyncSession wrappers
# ---------------------------------------------------------------------------


def _wire_ndjson_on_loop(session: AsyncSession, data: bytes, loop: asyncio.AbstractEventLoop) -> None:
    """Wire ndjson data on a specific event loop (for SyncSession tests)."""
    import concurrent.futures

    def _wire():
        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = None

        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        session._process_mgr._process.stdout = reader

        stdin_mock = MagicMock()
        stdin_mock.drain = AsyncMock()
        session._process_mgr._process.stdin = stdin_mock

    future = loop.call_soon_threadsafe(_wire)
    # Need to wait for execution -- use run_coroutine_threadsafe with a tiny coro
    f = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.01), loop)
    f.result(timeout=5)


class TestSyncSessionHooks:
    def test_on_turn_complete_passes_sync_session(self):
        received = {}

        def hook(session, result):
            received["session"] = session
            received["result"] = result

        async_session = _make_session()

        sync_session = SyncSession(SessionConfig(model="test", profile="test"))
        sync_session._async_session = async_session
        sync_session._started = True
        loop = sync_session._ensure_loop()

        # Wire ndjson on the SyncSession's event loop
        _wire_ndjson_on_loop(async_session, _build_ndjson([_SYSTEM_INIT, _RESULT]), loop)

        sync_session.on_turn_complete(hook)

        for _ in sync_session.send("hi"):
            pass

        sync_session.close()
        assert received["session"] is sync_session
        assert isinstance(received["result"], Result)

    def test_on_error_passes_sync_session(self):
        received = {}

        def hook(session, exc):
            received["session"] = session
            received["exc"] = exc

        async_session = _make_session()

        sync_session = SyncSession(SessionConfig(model="test", profile="test"))
        sync_session._async_session = async_session
        sync_session._started = True
        loop = sync_session._ensure_loop()

        _wire_ndjson_on_loop(async_session, _build_ndjson([_SYSTEM_INIT]), loop)

        sync_session.on_error(hook)

        with pytest.raises(ClaudeStreamError):
            for _ in sync_session.send("hi"):
                pass

        sync_session.close()
        assert received["session"] is sync_session
        assert isinstance(received["exc"], ClaudeStreamError)

    def test_on_close_passes_sync_session(self):
        received = {}

        def hook(session):
            received["session"] = session

        async_session = _make_session()
        async_session._process_mgr.close = AsyncMock()

        sync_session = SyncSession(SessionConfig(model="test", profile="test"))
        sync_session._async_session = async_session
        sync_session._started = True
        sync_session._ensure_loop()

        sync_session.on_close(hook)
        sync_session.close()

        assert received["session"] is sync_session

    def test_requires_started_session(self):
        sync_session = SyncSession(SessionConfig(model="test", profile="test"))

        with pytest.raises(RuntimeError, match="Session not started"):
            sync_session.on_turn_complete(lambda s, r: None)

        with pytest.raises(RuntimeError, match="Session not started"):
            sync_session.on_error(lambda s, e: None)

        with pytest.raises(RuntimeError, match="Session not started"):
            sync_session.on_close(lambda s: None)
