"""Tests for NDJSON protocol I/O."""

import asyncio
import json
import logging

from claudestream._protocol import parse_event, read_events, write_message
from claudestream.events import (
    ControlResponse,
    HookEvent,
    PermissionRequest,
    SystemInit,
    UnknownEvent,
    UserDialogRequest,
)
from claudestream.messages import UserMessage


class TestReadEvents:
    def test_parses_ndjson_lines(self):
        lines = [
            json.dumps({"type": "system", "subtype": "init", "cwd": "/tmp", "tools": [], "model": "test"}) + "\n",
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "done"}) + "\n",
        ]
        data = "".join(lines).encode("utf-8")

        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
        assert isinstance(events[0], SystemInit)

    def test_skips_empty_lines(self):
        data = b"\n\n" + json.dumps({"type": "result", "is_error": False}).encode() + b"\n\n"

        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1

    def test_skips_non_json_lines(self):
        data = b"[SandboxDebug] some debug output\n" + json.dumps({"type": "result", "is_error": False}).encode() + b"\n"

        async def run():
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1

    def test_eof_terminates(self):
        async def run():
            reader = asyncio.StreamReader()
            reader.feed_eof()
            events = []
            async for event in read_events(reader):
                events.append(event)
            return events

        events = asyncio.run(run())
        assert events == []


class TestWriteMessage:
    def test_writes_json_line(self):
        async def run():
            reader = asyncio.StreamReader()

            # Create a simple write target
            output = bytearray()

            class FakeTransport:
                def get_extra_info(self, *a, **kw):
                    return None
                def is_closing(self):
                    return False
                def write(self, data):
                    output.extend(data)
                def close(self):
                    pass

            protocol = asyncio.StreamReaderProtocol(reader)
            fake_transport = FakeTransport()
            writer = asyncio.StreamWriter(fake_transport, protocol, reader, asyncio.get_event_loop())

            msg = UserMessage(content="hello", session_id="s1")
            await write_message(writer, msg)

            line = output.decode("utf-8")
            assert line.endswith("\n")
            parsed = json.loads(line)
            assert parsed["type"] == "user"
            assert parsed["message"]["content"] == "hello"

        asyncio.run(run())

    def test_write_message_logs_at_info(self, caplog):
        """write_message should log the message type at INFO level."""
        async def run():
            reader = asyncio.StreamReader()

            output = bytearray()

            class FakeTransport:
                def get_extra_info(self, *a, **kw):
                    return None
                def is_closing(self):
                    return False
                def write(self, data):
                    output.extend(data)
                def close(self):
                    pass

            protocol = asyncio.StreamReaderProtocol(reader)
            fake_transport = FakeTransport()
            writer = asyncio.StreamWriter(fake_transport, protocol, reader, asyncio.get_event_loop())

            msg = UserMessage(content="test", session_id="s1")
            with caplog.at_level(logging.INFO, logger="claudestream"):
                await write_message(writer, msg)

            assert any(
                "protocol: Sending UserMessage" in record.message
                and record.levelno == logging.INFO
                for record in caplog.records
            )

        asyncio.run(run())


class TestControlResponseParse:
    def test_success_response(self):
        raw = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "ctrl_1",
                "response": {"still_queued": ["u1"]},
            },
        }
        event = parse_event(raw)
        assert isinstance(event, ControlResponse)
        assert event.request_id == "ctrl_1"
        assert event.subtype == "success"
        assert event.response == {"still_queued": ["u1"]}
        assert event.error == ""

    def test_error_response(self):
        raw = {
            "type": "control_response",
            "response": {
                "subtype": "error",
                "request_id": "ctrl_2",
                "error": "unsupported mode",
            },
        }
        event = parse_event(raw)
        assert isinstance(event, ControlResponse)
        assert event.request_id == "ctrl_2"
        assert event.subtype == "error"
        assert event.error == "unsupported mode"
        assert event.response == {}


class TestCanUseToolParse:
    """Item 9: the live CLI emits permission prompts as subtype 'can_use_tool'
    (reading 'input'), which must decode to PermissionRequest, not HookEvent."""

    def test_can_use_tool_decodes_to_permission_request(self):
        # Verbatim shape from the live-CLI probe (AskUserQuestion permission gate).
        raw = {
            "type": "control_request",
            "request_id": "824e848c-32cd-4841-a2b8-bf32ea7245a3",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "AskUserQuestion",
                "display_name": "AskUserQuestion",
                "input": {"questions": [{"question": "Which color?", "header": "Color"}]},
                "tool_use_id": "toolu_017T3YGCWxWvp79BbB8AKaEG",
            },
        }
        event = parse_event(raw)
        assert isinstance(event, PermissionRequest)
        assert not isinstance(event, HookEvent)
        assert event.request_id == "824e848c-32cd-4841-a2b8-bf32ea7245a3"
        assert event.tool_name == "AskUserQuestion"
        assert event.display_name == "AskUserQuestion"
        # 'input' (not 'tool_input') carries the tool arguments on the live wire.
        assert event.tool_input == {"questions": [{"question": "Which color?", "header": "Color"}]}
        assert event.tool_use_id == "toolu_017T3YGCWxWvp79BbB8AKaEG"

    def test_can_use_tool_populates_enriched_fields(self):
        raw = {
            "type": "control_request",
            "request_id": "req_9",
            "request": {
                "subtype": "can_use_tool",
                "tool_name": "Bash",
                "input": {"command": "rm -rf /"},
                "tool_use_id": "toolu_x",
                "permission_suggestions": [{"type": "addRule", "rule": {"toolName": "Bash"}}],
                "title": "Run a command",
                "description": "Execute a shell command",
                "decision_reason": "not allowlisted",
                "decision_reason_type": "safetyCheck",
                "requires_user_interaction": True,
            },
        }
        event = parse_event(raw)
        assert isinstance(event, PermissionRequest)
        assert event.permission_suggestions == [{"type": "addRule", "rule": {"toolName": "Bash"}}]
        assert event.title == "Run a command"
        assert event.description == "Execute a shell command"
        assert event.decision_reason == "not allowlisted"
        assert event.decision_reason_type == "safetyCheck"
        assert event.requires_user_interaction is True

    def test_legacy_permission_subtype_still_decodes(self):
        """The older 'permission' subtype (reading 'tool_input') still works and
        defaults the enriched fields."""
        raw = {
            "type": "control_request",
            "request_id": "perm_1",
            "request": {
                "subtype": "permission",
                "tool_name": "Read",
                "tool_input": {"file_path": "/etc/passwd"},
                "tool_use_id": "toolu_y",
            },
        }
        event = parse_event(raw)
        assert isinstance(event, PermissionRequest)
        assert event.tool_input == {"file_path": "/etc/passwd"}
        assert event.permission_suggestions == []
        assert event.decision_reason_type == ""
        assert event.requires_user_interaction is False


class TestUserDialogParse:
    """Item 9: subtype 'request_user_dialog' decodes to UserDialogRequest."""

    def test_request_user_dialog_decodes(self):
        raw = {
            "type": "control_request",
            "request_id": "dlg_1",
            "request": {
                "subtype": "request_user_dialog",
                "dialog_kind": "AskUserQuestion",
                "payload": {"questions": [{"question": "Pick one", "header": "H"}]},
                "tool_use_id": "toolu_z",
            },
        }
        event = parse_event(raw)
        assert isinstance(event, UserDialogRequest)
        assert event.request_id == "dlg_1"
        assert event.dialog_kind == "AskUserQuestion"
        assert event.payload == {"questions": [{"question": "Pick one", "header": "H"}]}
        assert event.tool_use_id == "toolu_z"

    def test_tool_use_id_optional(self):
        raw = {
            "type": "control_request",
            "request_id": "dlg_2",
            "request": {
                "subtype": "request_user_dialog",
                "dialog_kind": "refusal_fallback_prompt",
                "payload": {},
            },
        }
        event = parse_event(raw)
        assert isinstance(event, UserDialogRequest)
        assert event.dialog_kind == "refusal_fallback_prompt"
        assert event.tool_use_id is None
