"""Tests for session observability properties (Phase 6.2/6.3)."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._async_session import AsyncSession
from claudestream._sync_session import SyncSession
from claudestream._options import SessionConfig
from claudestream._tools import Tool
from claudestream.events import AssistantText, Result
from claudestream.policy import Sandbox


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
    "cwd": "/workspace/project",
    "tools": ["Read", "Write"],
    "mcp_servers": ["server-a", "server-b"],
    "model": "opus",
    "session_id": "sid-123",
    "permission_mode": "allowedTools",
    "claude_code_version": "2.5.0",
}

_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 100.0,
    "result": "done",
    "session_id": "sid-123",
}


# ---------------------------------------------------------------------------
# AsyncSession property tests
# ---------------------------------------------------------------------------


class TestSandboxProperty:
    def test_returns_config_sandbox(self):
        sb = Sandbox(tools=["Read"])
        session = _make_session(sandbox=sb)
        assert session.sandbox is sb

    def test_returns_none_when_no_sandbox(self):
        session = _make_session()
        assert session.sandbox is None


class TestUserToolsProperty:
    def test_returns_registered_tools(self):
        t = Tool(
            name="greet",
            description="Say hello",
            input_schema={"type": "object"},
            handler=lambda: "hi",
            server="test_server",
        )
        session = _make_session(tools=[t])
        assert len(session.user_tools) == 1
        assert session.user_tools[0].name == "greet"

    def test_returns_empty_list_when_no_tools(self):
        session = _make_session()
        assert session.user_tools == []


class TestIsAliveProperty:
    def test_false_before_start(self):
        session = _make_session()
        assert session.is_alive is False

    def test_true_when_process_running(self):
        session = _make_session()
        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = None
        assert session.is_alive is True

    def test_false_when_process_exited(self):
        session = _make_session()
        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = 0
        assert session.is_alive is False


class TestActiveTurnProperty:
    def test_false_initially(self):
        session = _make_session()
        assert session.active_turn is False


class TestCancelledProperty:
    def test_false_initially(self):
        session = _make_session()
        assert session.cancelled is False


class TestProcessPidProperty:
    def test_none_before_start(self):
        session = _make_session()
        assert session.process_pid is None

    def test_returns_pid_when_running(self):
        session = _make_session()
        mock_proc = MagicMock()
        mock_proc.pid = 42
        session._process_mgr._process = mock_proc
        assert session.process_pid == 42


class TestCwdProperty:
    def test_empty_before_system_init(self):
        session = _make_session()
        assert session.cwd == ""

    def test_populated_from_system_init(self):
        data = _build_ndjson([_SYSTEM_INIT, _RESULT])

        async def run():
            session = _make_session()
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            return session.cwd

        cwd = asyncio.run(run())
        assert cwd == "/workspace/project"


class TestMcpServersProperty:
    def test_empty_before_system_init(self):
        session = _make_session()
        assert session.mcp_servers == []

    def test_populated_from_system_init(self):
        data = _build_ndjson([_SYSTEM_INIT, _RESULT])

        async def run():
            session = _make_session()
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            return session.mcp_servers

        servers = asyncio.run(run())
        assert servers == ["server-a", "server-b"]


class TestPermissionModeProperty:
    def test_empty_before_system_init(self):
        session = _make_session()
        assert session.permission_mode == ""

    def test_populated_from_system_init(self):
        data = _build_ndjson([_SYSTEM_INIT, _RESULT])

        async def run():
            session = _make_session()
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            return session.permission_mode

        mode = asyncio.run(run())
        assert mode == "allowedTools"


class TestConfigProperty:
    def test_returns_session_config(self):
        session = _make_session()
        cfg = session.config
        assert isinstance(cfg, SessionConfig)
        assert cfg.model == "haiku"


# ---------------------------------------------------------------------------
# SyncSession.stderr_lines
# ---------------------------------------------------------------------------


class TestSyncSessionStderrLines:
    def test_delegates_to_async_session(self):
        mock_async = MagicMock()
        mock_async.stderr_lines = ["error line 1", "error line 2"]

        session = SyncSession(SessionConfig(model="test", profile="test"))
        session._async_session = mock_async
        assert session.stderr_lines == ["error line 1", "error line 2"]

    def test_returns_empty_before_start(self):
        session = SyncSession(SessionConfig(model="test", profile="test"))
        assert session.stderr_lines == []
