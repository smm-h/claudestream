"""Tests for MCP tool lifecycle: InitializeRequest, tools/list, tools/call."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claudestream._async_session import AsyncSession
from claudestream._tools import Tool
from claudestream.events import McpRequest, Result

from tests.conftest import make_test_session


def _build_ndjson(events: list[dict]) -> bytes:
    """Encode a list of raw event dicts as NDJSON bytes."""
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _handshake_events(server_name: str = "test_server") -> list[dict]:
    """Build the sequence of events that _start() reads during MCP handshake.

    The handshake sequence is:
    1. ControlResponse for InitializeRequest
    2. ControlResponse for McpSetServers
    3. McpRequest: initialize
    4. McpRequest: notifications/initialized
    5. McpRequest: tools/list
    """
    return [
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "init_1",
                "response": {},
            },
        },
        {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": "mcp_set_1",
                "response": {},
            },
        },
        {
            "type": "control_request",
            "request": {
                "subtype": "mcp_message",
                "request_id": "mcp_init_1",
                "server_name": server_name,
                "message": {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            },
            "session_id": "s1",
        },
        {
            "type": "control_request",
            "request": {
                "subtype": "mcp_message",
                "request_id": "mcp_notif_1",
                "server_name": server_name,
                "message": {"jsonrpc": "2.0", "id": 2, "method": "notifications/initialized"},
            },
            "session_id": "s1",
        },
        {
            "type": "control_request",
            "request": {
                "subtype": "mcp_message",
                "request_id": "mcp_tools_1",
                "server_name": server_name,
                "message": {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            },
            "session_id": "s1",
        },
    ]


def _prepare_session(session: AsyncSession, data: bytes) -> None:
    """Mock the process manager internals so _read_turn can read from data."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader
    # stdin needs drain() to be async (write_message awaits it)
    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


def _get_stdin_writes(session: AsyncSession) -> list[dict]:
    """Extract all JSON objects written to the mocked stdin."""
    stdin = session._process_mgr._process.stdin
    results = []
    for call in stdin.write.call_args_list:
        raw = call[0][0]
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        for line in text.strip().split("\n"):
            if line.strip():
                results.append(json.loads(line))
    return results


# -- Fixtures ----------------------------------------------------------------

RESULT_RAW = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 100.0,
    "duration_api_ms": 90.0,
    "num_turns": 1,
    "result": "Done.",
    "stop_reason": "end_turn",
    "total_cost_usd": 0.001,
    "session_id": "s1",
}


def _mcp_request_raw(server_name: str, message: dict, request_id: str = "mcp_1") -> dict:
    return {
        "type": "control_request",
        "request_id": request_id,
        "request": {
            "subtype": "mcp_message",
            "server_name": server_name,
            "message": message,
        },
        "session_id": "s1",
    }


def _sync_handler(x: str) -> str:
    return f"result:{x}"


async def _async_handler(x: str) -> str:
    return f"async_result:{x}"


def _make_tools() -> list[Tool]:
    return [
        Tool(
            name="my_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            handler=_sync_handler,
            server="test_server",
        ),
    ]


# -- Tests -------------------------------------------------------------------


class TestMcpWildcardsInAllowedTools:
    def test_mcp_wildcards_added(self):
        """MCP wildcard patterns are added to ProcessConfig.allowed_tools."""
        tools = _make_tools()
        session = make_test_session(tools=tools)
        assert "mcp__test_server__*" in session._process_mgr.config.allowed_tools

    def test_mcp_wildcards_multiple_servers(self):
        """Each server gets its own wildcard pattern."""
        tools = _make_tools() + [
            Tool(
                name="other_tool",
                description="Another tool",
                input_schema={},
                handler=lambda: None,
                server="other_server",
            ),
        ]
        session = make_test_session(tools=tools)
        allowed = session._process_mgr.config.allowed_tools
        assert "mcp__test_server__*" in allowed
        assert "mcp__other_server__*" in allowed

    def test_no_tools_no_wildcards(self):
        """No MCP wildcards when no tools are registered."""
        session = make_test_session()
        assert session._process_mgr.config.allowed_tools == []

    def test_wildcards_merged_with_sandbox_tools(self):
        """MCP wildcards are appended alongside sandbox-specified tools."""
        from claudestream.policy import Sandbox
        sandbox = Sandbox(tools=["Bash", "Read"])
        tools = _make_tools()
        session = make_test_session(sandbox=sandbox, tools=tools)
        allowed = session._process_mgr.config.allowed_tools
        assert "Bash" in allowed
        assert "Read" in allowed
        assert "mcp__test_server__*" in allowed


class TestPermissionPromptToolWithMcpTools:
    def test_permission_prompt_tool_stdio_when_tools_registered(self):
        """--permission-prompt-tool stdio is set when MCP tools are registered, even without sandbox tools/write_paths."""
        tools = _make_tools()
        session = make_test_session(tools=tools)
        cfg = session._process_mgr.config
        assert cfg.permission_prompt_tool == "stdio"
        argv = cfg.build_argv()
        assert "--permission-prompt-tool" in argv
        idx = argv.index("--permission-prompt-tool")
        assert argv[idx + 1] == "stdio"

    def test_permission_prompt_tool_stdio_with_skip_permissions_and_tools(self):
        """Both --dangerously-skip-permissions and --permission-prompt-tool stdio are present when sandbox.skip_permissions=True and tools are registered."""
        from claudestream.policy import Sandbox
        sandbox = Sandbox(skip_permissions=True)
        tools = _make_tools()
        session = make_test_session(sandbox=sandbox, tools=tools)
        cfg = session._process_mgr.config
        assert cfg.permission_prompt_tool == "stdio"
        assert cfg.dangerously_skip_permissions is True
        argv = cfg.build_argv()
        assert "--permission-prompt-tool" in argv
        assert "--dangerously-skip-permissions" in argv

    def test_no_permission_prompt_tool_without_tools(self):
        """--permission-prompt-tool is NOT set when no tools are registered and no sandbox tools/write_paths."""
        session = make_test_session()
        cfg = session._process_mgr.config
        assert cfg.permission_prompt_tool is None

    def test_permission_prompt_tool_from_sandbox_still_works(self):
        """--permission-prompt-tool stdio is set via sandbox.tools even without MCP tools."""
        from claudestream.policy import Sandbox
        sandbox = Sandbox(tools=["Bash", "Read"])
        session = make_test_session(sandbox=sandbox)
        cfg = session._process_mgr.config
        assert cfg.permission_prompt_tool == "stdio"


class TestInitializeRequest:
    def test_init_request_sent_on_start(self):
        """InitializeRequest is written to stdin when tools are registered."""
        tools = _make_tools()
        session = make_test_session(tools=tools)

        async def run():
            # Provide handshake data so _start() can complete the MCP handshake
            handshake_data = _build_ndjson(_handshake_events("test_server"))
            _prepare_session(session, handshake_data)
            with patch.object(session._process_mgr, "start", new_callable=AsyncMock):
                await session._start()
            return _get_stdin_writes(session)

        writes = asyncio.run(run())
        # Should have InitializeRequest and McpSetServers control requests
        init_writes = [
            w for w in writes
            if w.get("type") == "control_request" and w.get("request", {}).get("subtype") == "initialize"
        ]
        assert len(init_writes) == 1
        req = init_writes[0]["request"]
        assert req["subtype"] == "initialize"
        assert "test_server" in req["sdk_mcp_servers"]

    def test_no_init_request_without_tools(self):
        """No InitializeRequest when no tools and no hooks are registered."""
        session = make_test_session()

        async def run():
            _prepare_session(session, b"")
            with patch.object(session._process_mgr, "start", new_callable=AsyncMock):
                await session._start()
            return _get_stdin_writes(session)

        writes = asyncio.run(run())
        init_writes = [w for w in writes if w.get("type") == "control_request"]
        assert len(init_writes) == 0

    def test_init_request_sent_with_hooks_only(self):
        """InitializeRequest is sent when hooks are provided even without tools."""
        hooks = {"PreToolUse": {"command": "echo hook"}}
        session = make_test_session(hooks=hooks)

        async def run():
            _prepare_session(session, b"")
            with patch.object(session._process_mgr, "start", new_callable=AsyncMock):
                await session._start()
            return _get_stdin_writes(session)

        writes = asyncio.run(run())
        init_writes = [w for w in writes if w.get("type") == "control_request"]
        assert len(init_writes) == 1
        req = init_writes[0]["request"]
        assert req["subtype"] == "initialize"
        assert req["hooks"] == hooks
        assert req["sdk_mcp_servers"] == []

    def test_init_request_includes_hooks_and_tools(self):
        """InitializeRequest includes both hooks and tool server names."""
        hooks = {"PostToolUse": {"command": "echo done"}}
        tools = _make_tools()
        session = make_test_session(tools=tools, hooks=hooks)

        async def run():
            # Provide handshake data so _start() can complete the MCP handshake
            handshake_data = _build_ndjson(_handshake_events("test_server"))
            _prepare_session(session, handshake_data)
            with patch.object(session._process_mgr, "start", new_callable=AsyncMock):
                await session._start()
            return _get_stdin_writes(session)

        writes = asyncio.run(run())
        init_writes = [
            w for w in writes
            if w.get("type") == "control_request" and w.get("request", {}).get("subtype") == "initialize"
        ]
        assert len(init_writes) == 1
        req = init_writes[0]["request"]
        assert req["hooks"] == hooks
        assert "test_server" in req["sdk_mcp_servers"]


class TestToolsList:
    def test_tools_list_response(self):
        """tools/list request returns tool schemas and is NOT yielded."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # McpRequest for tools/list should NOT be yielded
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 0

        # Response should have been written to stdin
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        resp = responses[0]["response"]
        assert resp["request_id"] == "mcp_1"
        mcp_resp = resp["response"]["mcp_response"]
        assert mcp_resp["jsonrpc"] == "2.0"
        assert mcp_resp["id"] == 1
        result_tools = mcp_resp["result"]["tools"]
        assert len(result_tools) == 1
        assert result_tools[0]["name"] == "my_tool"
        assert result_tools[0]["description"] == "A test tool"
        assert result_tools[0]["inputSchema"] == tools[0].input_schema

    def test_tools_list_includes_always_load_meta(self):
        """tools/list response includes _meta.anthropic.alwaysLoad for each tool."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        result_tools = responses[0]["response"]["response"]["mcp_response"]["result"]["tools"]
        for t in result_tools:
            assert "_meta" in t, f"Tool {t['name']} missing _meta"
            assert t["_meta"] == {"anthropic": {"alwaysLoad": True}}

    def test_tools_list_unknown_server_yields_event(self):
        """tools/list for an unknown server yields the McpRequest to the consumer."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("unknown_server", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 1
        assert mcp_events[0].server_name == "unknown_server"


class TestToolsCall:
    def test_sync_handler_called(self):
        """tools/call invokes the sync handler and returns its result."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 42,
            "method": "tools/call",
            "params": {"name": "my_tool", "arguments": {"x": "hello"}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # tools/call McpRequest SHOULD be yielded
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 1

        # Check the response
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["id"] == 42
        assert mcp_resp["result"]["content"][0]["text"] == "result:hello"

    def test_async_handler_called(self):
        """tools/call invokes an async handler correctly."""
        tools = [
            Tool(
                name="async_tool",
                description="An async tool",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                handler=_async_handler,
                server="test_server",
            ),
        ]
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "async_tool", "arguments": {"x": "world"}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["result"]["content"][0]["text"] == "async_result:world"

    def test_handler_error_returns_error_response(self):
        """When a handler raises, an error JSON-RPC response is sent."""
        def failing_handler(x: str) -> str:
            raise ValueError("something broke")

        tools = [
            Tool(
                name="bad_tool",
                description="A failing tool",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                handler=failing_handler,
                server="test_server",
            ),
        ]
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "bad_tool", "arguments": {"x": "boom"}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # The McpRequest should still be yielded
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 1

        # Error response should be sent
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["id"] == 99
        assert "error" in mcp_resp
        assert mcp_resp["error"]["code"] == -32000
        assert "something broke" in mcp_resp["error"]["message"]

    def test_unknown_tool_returns_error(self):
        """tools/call for an unknown tool name returns an error response."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {"name": "nonexistent_tool", "arguments": {}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert "error" in mcp_resp
        assert mcp_resp["error"]["code"] == -32601
        assert "nonexistent_tool" in mcp_resp["error"]["message"]

    def test_unknown_method_yields_event(self):
        """An unknown JSON-RPC method passes through to the consumer."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "resources/list",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 1
        # No response should have been written
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 0


class TestInitializeHandler:
    def test_initialize_response(self):
        """MCP initialize request returns protocol info and is NOT yielded."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # McpRequest for initialize should NOT be yielded
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 0

        # Response should have been written to stdin
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["jsonrpc"] == "2.0"
        assert mcp_resp["id"] == 1
        result = mcp_resp["result"]
        assert "protocolVersion" in result
        assert result["protocolVersion"] == "2025-11-25"
        assert "capabilities" in result
        assert "tools" in result["capabilities"]
        assert "serverInfo" in result
        assert result["serverInfo"]["name"] == "test_server"
        assert result["serverInfo"]["version"] == "1.0.0"


class TestNotificationsInitializedHandler:
    def test_notifications_initialized_response(self):
        """notifications/initialized request returns empty result and is NOT yielded."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # McpRequest for notifications/initialized should NOT be yielded
        mcp_events = [e for e in events if isinstance(e, McpRequest)]
        assert len(mcp_events) == 0

        # Response should have been written to stdin
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["jsonrpc"] == "2.0"
        assert mcp_resp["result"] == {}
        # id should be 0 when rpc_id is None
        assert mcp_resp["id"] == 0

    def test_notifications_initialized_with_rpc_id(self):
        """notifications/initialized with explicit rpc_id preserves it."""
        tools = _make_tools()
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "notifications/initialized",
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["id"] == 5


class TestFullHandshake:
    def test_start_completes_handshake(self):
        """_start() sends InitializeRequest, McpSetServers, and handles the full MCP handshake."""
        tools = _make_tools()
        session = make_test_session(tools=tools)

        async def run():
            handshake_data = _build_ndjson(_handshake_events("test_server"))
            _prepare_session(session, handshake_data)
            with patch.object(session._process_mgr, "start", new_callable=AsyncMock):
                await session._start()
            return _get_stdin_writes(session)

        writes = asyncio.run(run())

        # 1. InitializeRequest was sent
        init_writes = [
            w for w in writes
            if w.get("type") == "control_request" and w.get("request", {}).get("subtype") == "initialize"
        ]
        assert len(init_writes) == 1
        assert "test_server" in init_writes[0]["request"]["sdk_mcp_servers"]

        # 2. McpSetServers was sent
        set_server_writes = [
            w for w in writes
            if w.get("type") == "control_request" and w.get("request", {}).get("subtype") == "mcp_set_servers"
        ]
        assert len(set_server_writes) == 1
        assert "test_server" in set_server_writes[0]["request"]["servers"]

        # 3. Three MCP handshake responses were sent (initialize, notifications/initialized, tools/list)
        mcp_responses = [
            w for w in writes
            if w.get("type") == "control_response" and "mcp_response" in w.get("response", {}).get("response", {})
        ]
        assert len(mcp_responses) == 3

        # Verify initialize response
        init_resp = mcp_responses[0]["response"]["response"]["mcp_response"]
        assert "protocolVersion" in init_resp.get("result", {})

        # Verify notifications/initialized response
        notif_resp = mcp_responses[1]["response"]["response"]["mcp_response"]
        assert notif_resp["result"] == {}

        # Verify tools/list response
        tools_resp = mcp_responses[2]["response"]["response"]["mcp_response"]
        assert len(tools_resp["result"]["tools"]) == 1
        assert tools_resp["result"]["tools"][0]["name"] == "my_tool"

    def test_handshake_stores_startup_events(self):
        """Non-MCP events during handshake are stored in _startup_events for later draining."""
        tools = _make_tools()
        session = make_test_session(tools=tools)

        # Insert a SystemInit event in the middle of the handshake stream
        handshake = _handshake_events("test_server")
        system_init = {
            "type": "system",
            "subtype": "init",
            "cwd": "/test",
            "tools": ["Bash"],
            "mcp_servers": ["test_server"],
            "model": "test-model",
            "session_id": "s1",
        }
        # Insert after the two ControlResponses but before the MCP messages
        events_with_init = handshake[:2] + [system_init] + handshake[2:]

        async def run():
            data = _build_ndjson(events_with_init)
            _prepare_session(session, data)
            with patch.object(session._process_mgr, "start", new_callable=AsyncMock):
                await session._start()
            return session._startup_events

        startup_events = asyncio.run(run())

        # The SystemInit should be stored (it arrived during the MCP handshake phase)
        from claudestream.events import SystemInit
        sys_inits = [e for e in startup_events if isinstance(e, SystemInit)]
        assert len(sys_inits) == 1
        assert sys_inits[0].model == "test-model"


class TestToolContextInjection:
    def test_tool_context_injected(self):
        """tool_context is injected into handler params listed in inject."""
        received_ctx = {}

        def handler_with_ctx(x: str, ctx=None) -> str:
            received_ctx["value"] = ctx
            return f"got:{x}"

        tools = [
            Tool(
                name="ctx_tool",
                description="A tool with context",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
                handler=handler_with_ctx,
                server="test_server",
                inject=["ctx"],
            ),
        ]
        my_context = {"conn": "db_conn", "session_id": "s123"}
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 10,
            "method": "tools/call",
            "params": {"name": "ctx_tool", "arguments": {"x": "hello"}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools, tool_context=my_context)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # Verify the handler received the context
        assert received_ctx["value"] is my_context

        # Verify the response was successful
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["result"]["content"][0]["text"] == "got:hello"

    def test_tool_context_missing_raises(self):
        """When tool has inject but no tool_context on SessionConfig, error response is sent."""
        def handler_with_ctx(x: str, ctx=None) -> str:
            return f"got:{x}"

        tools = [
            Tool(
                name="ctx_tool",
                description="A tool with context",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
                handler=handler_with_ctx,
                server="test_server",
                inject=["ctx"],
            ),
        ]
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "ctx_tool", "arguments": {"x": "hello"}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            # No tool_context set (defaults to None)
            session = make_test_session(tools=tools)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # Should get an error response
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert "error" in mcp_resp
        assert "tool_context" in mcp_resp["error"]["message"]

    def test_tool_context_with_async_handler(self):
        """tool_context is injected into async handler params listed in inject."""
        received_ctx = {}

        async def async_handler_with_ctx(x: str, ctx=None) -> str:
            received_ctx["value"] = ctx
            return f"async_got:{x}"

        tools = [
            Tool(
                name="async_ctx_tool",
                description="An async tool with context",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
                handler=async_handler_with_ctx,
                server="test_server",
                inject=["ctx"],
            ),
        ]
        my_context = {"tenant": "t1"}
        mcp_req = _mcp_request_raw("test_server", {
            "jsonrpc": "2.0",
            "id": 12,
            "method": "tools/call",
            "params": {"name": "async_ctx_tool", "arguments": {"x": "world"}},
        })
        data = _build_ndjson([mcp_req, RESULT_RAW])

        async def run():
            session = make_test_session(tools=tools, tool_context=my_context)
            _prepare_session(session, data)
            events = []
            async for event in session._read_turn(raw=False):
                events.append(event)
            return session, events

        session, events = asyncio.run(run())

        # Verify the async handler received the context
        assert received_ctx["value"] is my_context

        # Verify the response was successful
        writes = _get_stdin_writes(session)
        responses = [w for w in writes if w.get("type") == "control_response"]
        assert len(responses) == 1
        mcp_resp = responses[0]["response"]["response"]["mcp_response"]
        assert mcp_resp["result"]["content"][0]["text"] == "async_got:world"
