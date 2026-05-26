"""Tests for event parsing and protocol handling."""

import asyncio
import json
from unittest.mock import MagicMock

from claudestream._protocol import parse_event, flatten_event, parse_content_block, parse_usage

from tests.conftest import make_test_session
from claudestream.events import (
    SystemInit, ApiRetry, CompactBoundary,
    AssistantMessage, AssistantText, ToolUse, Thinking,
    ToolResultMessage, ToolResult,
    Result, StreamDelta, RateLimit,
    PermissionRequest, McpRequest, HookEvent, UnknownEvent,
    TextBlock, ToolUseBlock, ThinkingBlock, ToolResultBlock,
    Usage,
)


class TestParseEvent:
    def test_system_init(self):
        raw = {
            "type": "system",
            "subtype": "init",
            "cwd": "/home/test",
            "tools": ["Bash", "Read"],
            "mcp_servers": [],
            "model": "claude-sonnet-4-5",
            "permission_mode": "default",
            "claude_code_version": "2.1.128",
            "session_id": "abc-123",
            "uuid": "uuid-1",
        }
        event = parse_event(raw)
        assert isinstance(event, SystemInit)
        assert event.cwd == "/home/test"
        assert event.tools == ["Bash", "Read"]
        assert event.model == "claude-sonnet-4-5"
        assert event.claude_code_version == "2.1.128"
        assert event.session_id == "abc-123"

    def test_api_retry(self):
        raw = {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 1,
            "max_retries": 10,
            "retry_delay_ms": 500.0,
            "error_status": 429,
            "error": "rate_limit",
            "session_id": "abc",
            "uuid": "u1",
        }
        event = parse_event(raw)
        assert isinstance(event, ApiRetry)
        assert event.attempt == 1
        assert event.error == "rate_limit"
        assert event.error_status == 429

    def test_compact_boundary(self):
        raw = {"type": "system", "subtype": "compact_boundary", "session_id": "abc"}
        event = parse_event(raw)
        assert isinstance(event, CompactBoundary)

    def test_unknown_system_subtype(self):
        raw = {"type": "system", "subtype": "future_subtype", "session_id": "abc"}
        event = parse_event(raw)
        assert isinstance(event, UnknownEvent)

    def test_assistant_message(self):
        raw = {
            "type": "assistant",
            "session_id": "abc",
            "uuid": "u1",
            "parent_tool_use_id": None,
            "error": None,
            "message": {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "Hello world"},
                    {"type": "tool_use", "id": "tool_1", "name": "Bash", "input": {"command": "ls"}},
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_input_tokens": 10,
                    "cache_read_input_tokens": 5,
                },
            },
        }
        event = parse_event(raw)
        assert isinstance(event, AssistantMessage)
        assert len(event.content) == 2
        assert isinstance(event.content[0], TextBlock)
        assert event.content[0].text == "Hello world"
        assert isinstance(event.content[1], ToolUseBlock)
        assert event.content[1].name == "Bash"
        assert event.model == "claude-sonnet-4-5"
        assert event.usage is not None
        assert event.usage.input_tokens == 100
        assert event.usage.output_tokens == 50

    def test_user_tool_result(self):
        raw = {
            "type": "user",
            "session_id": "abc",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "tool_1", "content": "file1.txt\nfile2.txt"},
                ],
            },
        }
        event = parse_event(raw)
        assert isinstance(event, ToolResultMessage)
        assert len(event.content) == 1
        assert event.content[0].tool_use_id == "tool_1"

    def test_result(self):
        raw = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 5000.0,
            "duration_api_ms": 4500.0,
            "num_turns": 2,
            "result": "Done.",
            "stop_reason": "end_turn",
            "total_cost_usd": 0.05,
            "session_id": "abc",
            "uuid": "u1",
            "usage": {"input_tokens": 200, "output_tokens": 100},
        }
        event = parse_event(raw)
        assert isinstance(event, Result)
        assert event.total_cost_usd == 0.05
        assert event.num_turns == 2
        assert not event.is_error

    def test_stream_event(self):
        raw = {
            "type": "stream_event",
            "session_id": "abc",
            "uuid": "u1",
            "parent_tool_use_id": None,
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        }
        event = parse_event(raw)
        assert isinstance(event, StreamDelta)
        assert event.text == "Hi"
        assert event.delta_type == "text_delta"

    def test_permission_request(self):
        raw = {
            "type": "sdk_control_request",
            "request": {
                "subtype": "permission",
                "request_id": "perm_1",
                "tool_name": "Bash",
                "tool_input": {"command": "rm -rf /"},
                "decision_reason": "not in allowlist",
                "tool_use_id": "tool_1",
            },
            "session_id": "abc",
        }
        event = parse_event(raw)
        assert isinstance(event, PermissionRequest)
        assert event.tool_name == "Bash"
        assert event.request_id == "perm_1"

    def test_mcp_request(self):
        raw = {
            "type": "sdk_control_request",
            "request": {
                "subtype": "mcp_message",
                "request_id": "mcp_1",
                "server_name": "calculator",
                "message": {"jsonrpc": "2.0", "method": "tools/call"},
            },
            "session_id": "abc",
        }
        event = parse_event(raw)
        assert isinstance(event, McpRequest)
        assert event.server_name == "calculator"

    def test_hook_event_from_unknown_sdk_control_subtype(self):
        """Unknown sdk_control_request subtypes produce HookEvent."""
        raw = {
            "type": "sdk_control_request",
            "request": {
                "subtype": "PreToolUse",
                "request_id": "hook_1",
                "tool_name": "Bash",
                "hook_data": {"some": "data"},
            },
            "session_id": "abc",
        }
        event = parse_event(raw)
        assert isinstance(event, HookEvent)
        assert event.hook_name == "PreToolUse"
        assert event.hook_data["subtype"] == "PreToolUse"
        assert event.hook_data["request_id"] == "hook_1"

    def test_hook_event_fields(self):
        """HookEvent exposes hook_name and hook_data correctly."""
        raw = {
            "type": "sdk_control_request",
            "request": {
                "subtype": "PostToolUse",
                "request_id": "hook_2",
                "output": "tool completed",
            },
            "session_id": "s1",
            "uuid": "u1",
        }
        event = parse_event(raw)
        assert isinstance(event, HookEvent)
        assert event.hook_name == "PostToolUse"
        assert event.session_id == "s1"
        assert event.uuid == "u1"
        assert event.hook_data["output"] == "tool completed"

    def test_rate_limit(self):
        raw = {
            "type": "rate_limit",
            "rate_limit_info": {
                "status": "allowed_warning",
                "resets_at": 1716000000,
                "rate_limit_type": "five_hour",
                "utilization": 0.85,
            },
            "session_id": "abc",
        }
        event = parse_event(raw)
        assert isinstance(event, RateLimit)
        assert event.status == "allowed_warning"
        assert event.utilization == 0.85

    def test_unknown_type(self):
        raw = {"type": "future_event", "data": "something", "session_id": "abc"}
        event = parse_event(raw)
        assert isinstance(event, UnknownEvent)
        assert event.raw == raw

    def test_missing_fields_no_crash(self):
        """parse_event should handle missing fields gracefully."""
        raw = {"type": "system", "subtype": "init"}
        event = parse_event(raw)
        assert isinstance(event, SystemInit)
        assert event.cwd == ""
        assert event.tools == []

    def test_empty_assistant_message(self):
        raw = {"type": "assistant", "message": {"content": []}}
        event = parse_event(raw)
        assert isinstance(event, AssistantMessage)
        assert event.content == []


class TestParseContentBlock:
    def test_text_block(self):
        block = parse_content_block({"type": "text", "text": "hello"})
        assert isinstance(block, TextBlock)
        assert block.text == "hello"

    def test_tool_use_block(self):
        block = parse_content_block({"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "/tmp"}})
        assert isinstance(block, ToolUseBlock)
        assert block.name == "Read"
        assert block.id == "t1"

    def test_thinking_block(self):
        block = parse_content_block({"type": "thinking", "thinking": "let me think..."})
        assert isinstance(block, ThinkingBlock)
        assert block.thinking == "let me think..."

    def test_tool_result_block(self):
        block = parse_content_block({"type": "tool_result", "tool_use_id": "t1", "content": "output"})
        assert isinstance(block, ToolResultBlock)
        assert block.tool_use_id == "t1"


class TestParseUsage:
    def test_none(self):
        assert parse_usage(None) is None

    def test_empty_dict(self):
        assert parse_usage({}) is None

    def test_valid(self):
        usage = parse_usage({"input_tokens": 10, "output_tokens": 20, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 3})
        assert isinstance(usage, Usage)
        assert usage.input_tokens == 10
        assert usage.output_tokens == 20


class TestFlattenEvent:
    def test_assistant_message_flattens(self):
        event = AssistantMessage(
            type="assistant",
            session_id="abc",
            uuid="u1",
            content=[
                TextBlock(text="hello"),
                ToolUseBlock(id="t1", name="Bash", input={"command": "ls"}),
                ThinkingBlock(thinking="hmm"),
            ],
            parent_tool_use_id="parent_1",
        )
        flat = flatten_event(event)
        assert len(flat) == 3
        assert isinstance(flat[0], AssistantText)
        assert flat[0].text == "hello"
        assert flat[0].parent_tool_use_id == "parent_1"
        assert isinstance(flat[1], ToolUse)
        assert flat[1].name == "Bash"
        assert isinstance(flat[2], Thinking)
        assert flat[2].text == "hmm"

    def test_tool_result_message_flattens(self):
        event = ToolResultMessage(
            type="user",
            session_id="abc",
            content=[
                ToolResultBlock(tool_use_id="t1", content="output1"),
                ToolResultBlock(tool_use_id="t2", content="output2"),
            ],
        )
        flat = flatten_event(event)
        assert len(flat) == 2
        assert all(isinstance(e, ToolResult) for e in flat)
        assert flat[0].tool_use_id == "t1"
        assert flat[1].tool_use_id == "t2"

    def test_other_events_passthrough(self):
        event = Result(type="result", is_error=False, result="done")
        flat = flatten_event(event)
        assert flat == [event]

    def test_empty_content(self):
        event = AssistantMessage(type="assistant", content=[])
        flat = flatten_event(event)
        assert flat == []


class TestStreamDeltaProperties:
    def test_text_delta(self):
        event = StreamDelta(
            type="stream_event",
            event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}},
        )
        assert event.text == "Hi"
        assert event.delta_type == "text_delta"
        assert event.partial_json is None

    def test_input_json_delta(self):
        event = StreamDelta(
            type="stream_event",
            event={"type": "content_block_delta", "delta": {"type": "input_json_delta", "partial_json": '{"key"'}},
        )
        assert event.partial_json == '{"key"'
        assert event.text is None
        assert event.delta_type == "input_json_delta"

    def test_no_delta(self):
        event = StreamDelta(type="stream_event", event={"type": "message_start"})
        assert event.text is None
        assert event.delta_type is None
        assert event.event_type == "message_start"


class TestSystemInitLazyCapture:
    """Test that _read_turn() intercepts SystemInit, populates session metadata,
    and yields the event to consumers.
    """

    def _build_ndjson(self, events: list[dict]) -> bytes:
        """Encode a list of raw event dicts as NDJSON bytes."""
        return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")

    def test_system_init_yielded(self):
        """SystemInit events should be captured internally AND yielded to the consumer."""
        raw_events = [
            {
                "type": "system",
                "subtype": "init",
                "cwd": "/home/test",
                "tools": ["Bash", "Read"],
                "mcp_servers": [],
                "model": "claude-sonnet-4-5",
                "permission_mode": "default",
                "claude_code_version": "2.1.128",
                "session_id": "test-session-123",
                "uuid": "uuid-1",
            },
            {
                "type": "assistant",
                "session_id": "test-session-123",
                "uuid": "u2",
                "parent_tool_use_id": None,
                "error": None,
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-sonnet-4-5",
                    "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Hello!"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "duration_ms": 1000.0,
                "duration_api_ms": 900.0,
                "num_turns": 1,
                "result": "Hello!",
                "stop_reason": "end_turn",
                "total_cost_usd": 0.01,
                "session_id": "test-session-123",
                "uuid": "u3",
            },
        ]
        data = self._build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            # Mock the process manager so _read_turn thinks the process is alive
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None

            # Feed NDJSON data to a StreamReader for stdout
            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            session._process_mgr._process.stdout = reader

            # Mock stdin so write_message doesn't fail
            session._process_mgr._process.stdin = MagicMock()

            yielded_events = []
            async for event in session._read_turn(raw=False):
                yielded_events.append(event)

            return session, yielded_events

        session, yielded_events = asyncio.run(run())

        # SystemInit should appear in yielded events
        assert any(isinstance(e, SystemInit) for e in yielded_events)

        # And the session metadata should be populated from SystemInit
        assert session.session_id == "test-session-123"
        assert session.model_name == "claude-sonnet-4-5"
        assert session.tools == ["Bash", "Read"]

    def test_metadata_populated_after_first_send(self):
        """Session metadata (model_name, tools, session_id) should be None/empty
        before the first send, and populated after SystemInit is captured."""
        raw_events = [
            {
                "type": "system",
                "subtype": "init",
                "cwd": "/work",
                "tools": ["Bash", "Read", "Write"],
                "mcp_servers": [],
                "model": "claude-opus-4",
                "permission_mode": "default",
                "claude_code_version": "2.2.0",
                "session_id": "session-xyz",
                "uuid": "uuid-init",
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Done",
                "session_id": "session-xyz",
            },
        ]
        data = self._build_ndjson(raw_events)

        async def run():
            session = make_test_session()

            # Before any turn, metadata should be unset
            assert session.session_id is None
            assert session.model_name is None
            assert session.tools == []

            # Mock process manager
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None

            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            session._process_mgr._process.stdout = reader
            session._process_mgr._process.stdin = MagicMock()

            async for _ in session._read_turn(raw=False):
                pass

            return session

        session = asyncio.run(run())

        # After the turn, metadata should be populated
        assert session.session_id == "session-xyz"
        assert session.model_name == "claude-opus-4"
        assert session.tools == ["Bash", "Read", "Write"]

    def test_system_init_yielded_among_other_events(self):
        """When a stream has SystemInit among other events, all events
        including SystemInit should be yielded."""
        raw_events = [
            {
                "type": "system",
                "subtype": "init",
                "cwd": "/test",
                "tools": [],
                "model": "test-model",
                "session_id": "s1",
            },
            {
                "type": "assistant",
                "session_id": "s1",
                "message": {
                    "content": [{"type": "text", "text": "First"}],
                    "model": "test-model",
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "assistant",
                "session_id": "s1",
                "message": {
                    "content": [{"type": "text", "text": "Second"}],
                    "model": "test-model",
                    "stop_reason": "end_turn",
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            },
        ]
        data = self._build_ndjson(raw_events)

        async def run():
            session = make_test_session()
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None

            reader = asyncio.StreamReader()
            reader.feed_data(data)
            reader.feed_eof()
            session._process_mgr._process.stdout = reader
            session._process_mgr._process.stdin = MagicMock()

            yielded = []
            async for event in session._read_turn(raw=False):
                yielded.append(event)
            return yielded

        yielded = asyncio.run(run())

        # Should yield 1 SystemInit + 2 AssistantText (flattened from 2 AssistantMessages) + 1 Result
        types = [type(e).__name__ for e in yielded]
        assert types.count("SystemInit") == 1
        assert types.count("AssistantText") == 2
        assert types.count("Result") == 1


class TestHealthProbe:
    """Test that the startup health probe fires a warning when no events arrive."""

    def test_health_probe_fires_on_no_events(self, caplog):
        """Health probe should log a warning when no events arrive within the timeout."""
        import logging

        async def run():
            session = make_test_session()
            session._process_mgr._process = MagicMock()
            session._process_mgr._process.returncode = None

            # Create a reader that never sends data, then feeds EOF after a delay
            reader = asyncio.StreamReader()

            async def feed_eof_later():
                await asyncio.sleep(0.3)
                reader.feed_eof()

            asyncio.ensure_future(feed_eof_later())

            session._process_mgr._process.stdout = reader
            session._process_mgr._process.stdin = MagicMock()

            with caplog.at_level(logging.WARNING, logger="claudestream"):
                try:
                    async for _ in session._read_turn(raw=False, _health_timeout=0.1):
                        pass
                except Exception:
                    pass  # ClaudeStreamError expected (no Result event)

        asyncio.run(run())

        assert any(
            "No events received after" in record.message
            and "subprocess may be stuck" in record.message
            and record.levelno == logging.WARNING
            for record in caplog.records
        )
