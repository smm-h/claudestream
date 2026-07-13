"""Tests for control-request correlation infrastructure (Stage 1).

Covers the pending-request registry, turn-loop resolution/swallowing,
between-turns scoped reads, timeout handling, and restart/close safety.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._async_session import ClaudeStreamError
from claudestream.events import AssistantMessage, ControlResponse
from tests.conftest import make_test_session

# resolve_profile is only patched during construction by make_test_session; any
# call that rebuilds the process config (e.g. _restart_subprocess) needs it too.
_PROFILE_PATCH = patch("claudewheel.profile.resolve_profile", return_value={})


def _build_ndjson(events: list[dict]) -> bytes:
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _prepare_session(session, data: bytes) -> None:
    """Wire a fake process with a fed stdout StreamReader and a capturing stdin."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader
    stdin = MagicMock()
    stdin.write = MagicMock()
    stdin.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin


def _assistant(text: str) -> dict:
    return {
        "type": "assistant",
        "session_id": "s1",
        "error": None,
        "message": {
            "content": [{"type": "text", "text": text}],
            "model": "m",
            "stop_reason": "end_turn",
        },
    }


def _result() -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "session_id": "s1",
    }


def _ctrl_success(request_id: str, response: dict) -> dict:
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": response},
    }


def _ctrl_error(request_id: str, error: str) -> dict:
    return {
        "type": "control_response",
        "response": {"subtype": "error", "request_id": request_id, "error": error},
    }


class TestTurnLoopResolution:
    def test_matched_response_resolved_and_swallowed_unmatched_yielded(self):
        """A ControlResponse matching a pending id resolves its future and is
        swallowed; an unmatched ControlResponse is yielded to the consumer."""

        async def run():
            session = make_test_session()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            session._pending_controls["ctrl_1"] = fut

            events = [
                _ctrl_success("ctrl_1", {"ok": True}),
                _ctrl_success("other_9", {}),
                _assistant("hi"),
                _result(),
            ]
            _prepare_session(session, _build_ndjson(events))

            collected = []
            async for e in session._read_turn(raw=False):
                collected.append(e)
            return session, fut, collected

        session, fut, collected = asyncio.run(run())

        assert fut.done()
        assert fut.result() == {"ok": True}
        assert "ctrl_1" not in session._pending_controls

        ctrl_events = [e for e in collected if isinstance(e, ControlResponse)]
        assert len(ctrl_events) == 1
        assert ctrl_events[0].request_id == "other_9"


class TestControlRequestTimeout:
    def test_active_turn_timeout_raises_and_cleans_up(self):
        """When a turn is active and no response ever arrives, the request times
        out, raises ClaudeStreamError, and removes the pending entry."""

        async def run():
            session = make_test_session()
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None
            stdin = MagicMock()
            stdin.write = MagicMock()
            stdin.drain = AsyncMock()
            session._process_mgr._process.stdin = stdin
            session._active_turn = True  # turn-loop path; future never resolved

            with pytest.raises(ClaudeStreamError, match="timed out"):
                await session._control_request("interrupt", timeout=0.05)

            assert session._pending_controls == {}

        asyncio.run(run())


class TestControlRequestError:
    def test_error_subtype_raises_with_message(self):
        """An error-subtype control_response raises ClaudeStreamError including
        the CLI error text and cleans up the pending entry."""

        async def run():
            session = make_test_session()
            _prepare_session(session, _build_ndjson([_ctrl_error("ctrl_1", "unsupported mode")]))

            with pytest.raises(ClaudeStreamError, match="unsupported mode"):
                await session._control_request("set_permission_mode", {"mode": "bogus"})

            assert "ctrl_1" not in session._pending_controls

        asyncio.run(run())


class TestBetweenTurnsRead:
    def test_scoped_reader_buffers_unrelated_events(self):
        """Between turns, _control_request drives its own read loop, resolves on
        the matching id, and buffers unrelated events for the next turn."""

        async def run():
            session = make_test_session()
            events = [
                _assistant("buffered"),
                _ctrl_success("ctrl_1", {"still_queued": ["u1"]}),
            ]
            _prepare_session(session, _build_ndjson(events))

            result = await session._control_request("interrupt")
            return session, result

        session, result = asyncio.run(run())

        assert result == {"still_queued": ["u1"]}
        assert "ctrl_1" not in session._pending_controls
        assert len(session._startup_events) == 1
        assert isinstance(session._startup_events[0], AssistantMessage)

    def test_eof_raises(self):
        """A between-turns control read raises on stdout EOF."""

        async def run():
            session = make_test_session()
            _prepare_session(session, b"")  # immediate EOF

            with pytest.raises(ClaudeStreamError, match="closed stdout"):
                await session._control_request("interrupt")

            assert session._pending_controls == {}

        asyncio.run(run())


class TestRestartAndCloseSafety:
    def test_restart_fails_pending_controls(self):
        """_restart_subprocess fails all pending control futures and clears them."""

        async def run():
            session = make_test_session()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            session._pending_controls["ctrl_1"] = fut
            session._process_mgr.close = AsyncMock()
            session._start = AsyncMock()

            with _PROFILE_PATCH:
                await session._restart_subprocess()
            return session, fut

        session, fut = asyncio.run(run())

        assert fut.done()
        with pytest.raises(ClaudeStreamError, match="subprocess restarted"):
            fut.result()
        assert session._pending_controls == {}

    def test_close_fails_pending_controls(self):
        """close() fails all pending control futures and clears them."""

        async def run():
            session = make_test_session()
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            session._pending_controls["ctrl_1"] = fut
            session._process_mgr.close = AsyncMock()

            await session.close()
            return session, fut

        session, fut = asyncio.run(run())

        assert fut.done()
        with pytest.raises(ClaudeStreamError, match="session closed"):
            fut.result()
        assert session._pending_controls == {}
