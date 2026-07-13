"""Tests for Stage 2 public control methods.

Covers interrupt / set_permission_mode / set_model / get_context_usage on
AsyncSession, the SessionConfig.permission_mode -> --permission-mode plumbing,
and the SyncSession twins (spec Tests items 5-8 and part of 13).
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._async_session import ClaudeStreamError
from claudestream._sync_session import SyncSession
from claudestream._options import SessionConfig
from claudestream.events import ContextUsage, ControlResponse
from tests.conftest import make_test_session


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


def _written_frames(session) -> list[dict]:
    """Decode every control frame written to the fake stdin."""
    stdin = session._process_mgr._process.stdin
    return [json.loads(c.args[0].decode("utf-8")) for c in stdin.write.call_args_list]


def _ctrl_success(request_id: str, response: dict) -> dict:
    return {
        "type": "control_response",
        "response": {"subtype": "success", "request_id": request_id, "response": response},
    }


class TestInterruptDuringActiveTurn:
    def test_frame_written_and_still_queued_returned(self):
        """During an active turn the interrupt frame reaches stdin and the
        still_queued list from the resolving response is returned."""

        async def run():
            session = make_test_session()
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None
            stdin = MagicMock()
            stdin.write = MagicMock()
            stdin.drain = AsyncMock()
            session._process_mgr._process.stdin = stdin
            session._active_turn = True  # turn loop owns stdout; it resolves the future

            task = asyncio.create_task(session.interrupt())
            # Wait for the request to register + write its frame.
            for _ in range(100):
                await asyncio.sleep(0.001)
                if "ctrl_1" in session._pending_controls:
                    break
            # Resolve exactly as the turn loop would on the matching response.
            session._resolve_control(
                ControlResponse(type="control_response", request_id="ctrl_1", subtype="success", response={"still_queued": ["u1"]})
            )
            result = await task
            return session, stdin, result

        session, stdin, result = asyncio.run(run())

        assert result == ["u1"]
        assert "ctrl_1" not in session._pending_controls
        frames = [json.loads(c.args[0].decode("utf-8")) for c in stdin.write.call_args_list]
        interrupt_frames = [f for f in frames if f.get("request", {}).get("subtype") == "interrupt"]
        assert len(interrupt_frames) == 1
        assert interrupt_frames[0]["type"] == "control_request"
        assert interrupt_frames[0]["request"]["request_id"] == "ctrl_1"
        assert interrupt_frames[0]["request"] == {"subtype": "interrupt", "request_id": "ctrl_1"}

    def test_absent_still_queued_returns_empty_list(self):
        """A response without still_queued yields an empty list."""

        async def run():
            session = make_test_session()
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None
            stdin = MagicMock()
            stdin.write = MagicMock()
            stdin.drain = AsyncMock()
            session._process_mgr._process.stdin = stdin
            session._active_turn = True

            task = asyncio.create_task(session.interrupt())
            for _ in range(100):
                await asyncio.sleep(0.001)
                if "ctrl_1" in session._pending_controls:
                    break
            session._resolve_control(
                ControlResponse(type="control_response", request_id="ctrl_1", subtype="success", response={})
            )
            return await task

        assert asyncio.run(run()) == []


class TestSetPermissionModeAndModel:
    def test_set_permission_mode_updates_property(self):
        """A successful set_permission_mode updates the permission_mode property
        and sends the mode in the payload."""

        async def run():
            session = make_test_session()
            _prepare_session(session, _build_ndjson([_ctrl_success("ctrl_1", {})]))
            await session.set_permission_mode("plan")
            return session

        session = asyncio.run(run())
        assert session.permission_mode == "plan"
        frames = _written_frames(session)
        assert frames[0]["request"]["subtype"] == "set_permission_mode"
        assert frames[0]["request"]["mode"] == "plan"

    def test_set_model_updates_property(self):
        """A successful set_model updates model_name and sends the model."""

        async def run():
            session = make_test_session()
            _prepare_session(session, _build_ndjson([_ctrl_success("ctrl_1", {})]))
            await session.set_model("opus")
            return session

        session = asyncio.run(run())
        assert session.model_name == "opus"
        frames = _written_frames(session)
        assert frames[0]["request"]["subtype"] == "set_model"
        assert frames[0]["request"]["model"] == "opus"

    def test_set_model_none_omits_model_and_resets_property(self):
        """set_model(None) sends no model key and sets model_name to None."""

        async def run():
            session = make_test_session()
            session._model_name = "opus"
            _prepare_session(session, _build_ndjson([_ctrl_success("ctrl_1", {})]))
            await session.set_model(None)
            return session

        session = asyncio.run(run())
        assert session.model_name is None
        frames = _written_frames(session)
        assert frames[0]["request"]["subtype"] == "set_model"
        assert "model" not in frames[0]["request"]


class TestGetContextUsage:
    def test_maps_response_fields(self):
        """get_context_usage maps CLI keys into a ContextUsage struct."""

        payload = {
            "totalTokens": 1234,
            "maxTokens": 200000,
            "percentage": 0.617,
            "isAutoCompactEnabled": True,
            "categories": [
                {"name": "system", "tokens": 400},
                {"name": "messages", "tokens": 834},
            ],
        }

        async def run():
            session = make_test_session()
            _prepare_session(session, _build_ndjson([_ctrl_success("ctrl_1", payload)]))
            return await session.get_context_usage()

        usage = asyncio.run(run())
        assert isinstance(usage, ContextUsage)
        assert usage.total_tokens == 1234
        assert usage.max_tokens == 200000
        assert usage.percentage == 0.617
        assert usage.auto_compact_enabled is True
        assert [(c.name, c.tokens) for c in usage.categories] == [("system", 400), ("messages", 834)]
        assert usage.raw == payload

    def test_defaults_when_optional_fields_missing(self):
        """Only totalTokens/maxTokens are required; the rest default sanely."""

        async def run():
            session = make_test_session()
            _prepare_session(
                session, _build_ndjson([_ctrl_success("ctrl_1", {"totalTokens": 10, "maxTokens": 100})])
            )
            return await session.get_context_usage()

        usage = asyncio.run(run())
        assert usage.total_tokens == 10
        assert usage.max_tokens == 100
        assert usage.percentage == 0.0
        assert usage.categories == []
        assert usage.auto_compact_enabled is False

    def test_missing_total_tokens_raises(self):
        """A response without totalTokens is a hard error including the payload."""

        async def run():
            session = make_test_session()
            _prepare_session(
                session, _build_ndjson([_ctrl_success("ctrl_1", {"maxTokens": 100})])
            )
            with pytest.raises(ClaudeStreamError, match="totalTokens"):
                await session.get_context_usage()

        asyncio.run(run())

    def test_missing_max_tokens_raises(self):
        """A response without maxTokens is a hard error too."""

        async def run():
            session = make_test_session()
            _prepare_session(
                session, _build_ndjson([_ctrl_success("ctrl_1", {"totalTokens": 100})])
            )
            with pytest.raises(ClaudeStreamError, match="maxTokens"):
                await session.get_context_usage()

        asyncio.run(run())


class TestPermissionModePlumbing:
    def test_permission_mode_appears_in_argv(self):
        """SessionConfig.permission_mode flows through to --permission-mode."""
        session = make_test_session(permission_mode="acceptEdits")
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            argv = session._build_process_config().build_argv()
        assert "--permission-mode" in argv
        assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"

    def test_permission_mode_absent_by_default(self):
        """No permission_mode means no --permission-mode flag."""
        session = make_test_session()
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            argv = session._build_process_config().build_argv()
        assert "--permission-mode" not in argv


class TestInterceptPermissionsPlumbing:
    def test_intercept_permissions_adds_stdio_flag(self):
        """intercept_permissions=True forces --permission-prompt-tool stdio."""
        session = make_test_session(intercept_permissions=True)
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            argv = session._build_process_config().build_argv()
        assert "--permission-prompt-tool" in argv
        assert argv[argv.index("--permission-prompt-tool") + 1] == "stdio"

    def test_permission_prompt_tool_absent_by_default(self):
        """Without intercept_permissions, sandbox, or SDK tools, no stdio flag."""
        session = make_test_session()
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            argv = session._build_process_config().build_argv()
        assert "--permission-prompt-tool" not in argv


class TestSyncTwins:
    """Item 13 (Stage 2 methods): each sync twin delegates to its async method."""

    def _wire(self):
        session = SyncSession(SessionConfig(model="test", profile="test"))
        session._async_session = AsyncMock()
        session._ensure_loop()
        return session

    def _teardown(self, session):
        if session._loop is not None:
            session._loop.call_soon_threadsafe(session._loop.stop)
            if session._thread is not None:
                session._thread.join(timeout=5.0)

    def test_interrupt_delegates(self):
        session = self._wire()
        try:
            session._async_session.interrupt.return_value = ["u1"]
            assert session.interrupt() == ["u1"]
            session._async_session.interrupt.assert_awaited_once_with(timeout=30.0)
        finally:
            self._teardown(session)

    def test_set_permission_mode_delegates(self):
        session = self._wire()
        try:
            session.set_permission_mode("plan")
            session._async_session.set_permission_mode.assert_awaited_once_with("plan")
        finally:
            self._teardown(session)

    def test_set_model_delegates(self):
        session = self._wire()
        try:
            session.set_model("opus")
            session._async_session.set_model.assert_awaited_once_with("opus")
        finally:
            self._teardown(session)

    def test_get_context_usage_delegates(self):
        session = self._wire()
        try:
            sentinel = object()
            session._async_session.get_context_usage.return_value = sentinel
            assert session.get_context_usage() is sentinel
            session._async_session.get_context_usage.assert_awaited_once_with(timeout=30.0)
        finally:
            self._teardown(session)

    def test_methods_require_started_session(self):
        session = SyncSession(SessionConfig(model="test", profile="test"))
        with pytest.raises(RuntimeError, match="not started"):
            session.interrupt()
        with pytest.raises(RuntimeError, match="not started"):
            session.set_permission_mode("plan")
        with pytest.raises(RuntimeError, match="not started"):
            session.set_model("opus")
        with pytest.raises(RuntimeError, match="not started"):
            session.get_context_usage()
