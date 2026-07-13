"""Tests for Stage 3 user dialogs + enriched permissions (session level).

Covers respond_dialog / respond_dialog_cancelled / respond_allow(updated_permissions)
wire frames, UserDialogRequest surfacing to the consumer, the supported_dialog_kinds
initialize handshake, and the SyncSession twins (spec Tests items 9-11 and the dialog
part of item 13).
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._sync_session import SyncSession
from claudestream._options import SessionConfig
from claudestream.events import Result, UserDialogRequest
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
    stdin = session._process_mgr._process.stdin
    return [json.loads(c.args[0].decode("utf-8")) for c in stdin.write.call_args_list]


def _result() -> dict:
    return {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "done",
        "session_id": "s1",
    }


def _dialog_request(request_id: str) -> dict:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "request_user_dialog",
            "dialog_kind": "AskUserQuestion",
            "payload": {"questions": [{"question": "Pick", "header": "H"}]},
            "tool_use_id": "toolu_1",
        },
    }


class TestRespondDialog:
    def test_respond_dialog_wire_shape(self):
        """respond_dialog writes a completed control_response with the result."""

        async def run():
            session = make_test_session()
            _prepare_session(session, b"")
            await session.respond_dialog("dlg_1", {"answer": "Blue"})
            return session

        session = asyncio.run(run())
        frames = _written_frames(session)
        assert len(frames) == 1
        assert frames[0]["type"] == "control_response"
        assert frames[0]["response"]["subtype"] == "success"
        assert frames[0]["response"]["request_id"] == "dlg_1"
        assert frames[0]["response"]["response"] == {
            "behavior": "completed",
            "result": {"answer": "Blue"},
        }

    def test_respond_dialog_cancelled_wire_shape(self):
        """respond_dialog_cancelled writes a cancelled control_response."""

        async def run():
            session = make_test_session()
            _prepare_session(session, b"")
            await session.respond_dialog_cancelled("dlg_2")
            return session

        session = asyncio.run(run())
        frames = _written_frames(session)
        assert len(frames) == 1
        assert frames[0]["response"]["request_id"] == "dlg_2"
        assert frames[0]["response"]["response"] == {"behavior": "cancelled"}


class TestRespondAllowUpdatedPermissions:
    """Item 11: respond_allow passes updated_permissions through to the wire."""

    def test_updated_permissions_serialized(self):
        rules = [{"type": "addRule", "rule": {"toolName": "Bash"}}]

        async def run():
            session = make_test_session()
            _prepare_session(session, b"")
            await session.respond_allow("perm_1", {"command": "ls"}, updated_permissions=rules)
            return session

        session = asyncio.run(run())
        inner = _written_frames(session)[0]["response"]["response"]
        assert inner["behavior"] == "allow"
        assert inner["updatedInput"] == {"command": "ls"}
        assert inner["updatedPermissions"] == rules

    def test_updated_permissions_omitted_by_default(self):
        async def run():
            session = make_test_session()
            _prepare_session(session, b"")
            await session.respond_allow("perm_1", {"command": "ls"})
            return session

        session = asyncio.run(run())
        inner = _written_frames(session)[0]["response"]["response"]
        assert "updatedPermissions" not in inner


class TestUserDialogSurfaced:
    """UserDialogRequest is yielded to the consumer and never auto-handled."""

    def test_dialog_request_yielded_in_turn(self):
        async def run():
            session = make_test_session()
            _prepare_session(session, _build_ndjson([_dialog_request("dlg_1"), _result()]))
            collected = []
            async for e in session._read_turn(raw=False):
                collected.append(e)
            return collected

        collected = asyncio.run(run())
        dialogs = [e for e in collected if isinstance(e, UserDialogRequest)]
        assert len(dialogs) == 1
        assert dialogs[0].request_id == "dlg_1"
        assert dialogs[0].dialog_kind == "AskUserQuestion"
        assert any(isinstance(e, Result) for e in collected)

    def test_dialog_not_touched_by_sandbox(self):
        """A dialog request must not be routed through _handle_permission even
        when a sandbox is configured (dialogs are never auto-handled)."""
        from claudestream.policy import create_sandbox

        async def run():
            session = make_test_session(sandbox=create_sandbox(tools=["Read"]))
            session._handle_permission = AsyncMock()
            _prepare_session(session, _build_ndjson([_dialog_request("dlg_1"), _result()]))
            async for _ in session._read_turn(raw=False):
                pass
            return session

        session = asyncio.run(run())
        session._handle_permission.assert_not_awaited()


class TestSupportedDialogKindsHandshake:
    """Item 10: supported_dialog_kinds forces the initialize handshake even with
    no tools or hooks, and declares supportedDialogKinds on the wire."""

    def _wire_start(self, session):
        stdin = MagicMock()
        stdin.write = MagicMock()
        stdin.drain = AsyncMock()
        session._process_mgr = MagicMock()
        session._process_mgr.start = AsyncMock()
        session._process_mgr.stdin = stdin
        return stdin

    def test_initialize_sent_with_dialog_kinds(self):
        kinds = ["AskUserQuestion", "refusal_fallback_prompt"]

        async def run():
            session = make_test_session(supported_dialog_kinds=kinds)
            stdin = self._wire_start(session)
            with patch(
                "claudestream._async_session.check_version",
                new_callable=AsyncMock,
                return_value="2.1.0",
            ):
                await session._start()
            return stdin

        stdin = asyncio.run(run())
        frames = [json.loads(c.args[0].decode("utf-8")) for c in stdin.write.call_args_list]
        init_frames = [f for f in frames if f.get("request", {}).get("subtype") == "initialize"]
        assert len(init_frames) == 1
        assert init_frames[0]["request"]["supportedDialogKinds"] == kinds

    def test_no_initialize_without_dialog_kinds_tools_or_hooks(self):
        async def run():
            session = make_test_session()  # no tools, hooks, or dialog kinds
            stdin = self._wire_start(session)
            with patch(
                "claudestream._async_session.check_version",
                new_callable=AsyncMock,
                return_value="2.1.0",
            ):
                await session._start()
            return stdin

        stdin = asyncio.run(run())
        assert stdin.write.call_count == 0


class TestSyncDialogTwins:
    """Item 13 (dialog part): each sync twin delegates to its async method."""

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

    def test_respond_dialog_delegates(self):
        session = self._wire()
        try:
            session.respond_dialog("dlg_1", {"answer": "Blue"})
            session._async_session.respond_dialog.assert_awaited_once_with("dlg_1", {"answer": "Blue"})
        finally:
            self._teardown(session)

    def test_respond_dialog_cancelled_delegates(self):
        session = self._wire()
        try:
            session.respond_dialog_cancelled("dlg_2")
            session._async_session.respond_dialog_cancelled.assert_awaited_once_with("dlg_2")
        finally:
            self._teardown(session)

    def test_respond_allow_delegates_with_updated_permissions(self):
        session = self._wire()
        try:
            rules = [{"type": "addRule"}]
            session.respond_allow("perm_1", {"command": "ls"}, updated_permissions=rules)
            session._async_session.respond_allow.assert_awaited_once_with(
                "perm_1", {"command": "ls"}, updated_permissions=rules
            )
        finally:
            self._teardown(session)

    def test_respond_allow_default_updated_permissions(self):
        session = self._wire()
        try:
            session.respond_allow("perm_1", {"command": "ls"})
            session._async_session.respond_allow.assert_awaited_once_with(
                "perm_1", {"command": "ls"}, updated_permissions=None
            )
        finally:
            self._teardown(session)

    def test_dialog_methods_require_started_session(self):
        session = SyncSession(SessionConfig(model="test", profile="test"))
        with pytest.raises(RuntimeError, match="not started"):
            session.respond_dialog("dlg_1", {})
        with pytest.raises(RuntimeError, match="not started"):
            session.respond_dialog_cancelled("dlg_1")
