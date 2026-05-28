"""Integration test for MCP handshake protocol with a real Claude Code process.

Based on scripts/test_mcp_protocol_v8.py. Requires valid Claude Code auth.

Run with: uv run pytest tests/test_mcp_handshake_integration.py -v --timeout=120
Skip with: pytest -m "not integration"
"""

import asyncio
import json
import os

import pytest
from claudewheel.profile import resolve_profile

pytestmark = pytest.mark.integration

BINARY = "claude"
MODEL = "haiku"
PROFILE = "personal"

TOOLS = [{
    "name": "greet",
    "description": "Greet someone by name",
    "inputSchema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Name to greet"}},
        "required": ["name"],
    },
}]


def _mcp_resp(request_id: str, rpc_id: int | None, result: dict) -> dict:
    """Build an MCP control_response dict."""
    return {
        "type": "control_response",
        "response": {
            "subtype": "success",
            "request_id": request_id,
            "response": {
                "mcp_response": {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": result,
                },
            },
        },
    }


class TestMcpHandshakeIntegration:
    """Full MCP handshake with a real Claude Code process."""

    @pytest.mark.timeout(120)
    def test_full_mcp_handshake_and_tool_call(self):
        """Start Claude, complete MCP handshake, send user message, handle tools/call."""
        asyncio.run(self._run_handshake_test())

    async def _run_handshake_test(self) -> None:
        env = {**os.environ, **resolve_profile(PROFILE)}
        proc = await asyncio.create_subprocess_exec(
            BINARY,
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--model", MODEL,
            "--allowedTools", "mcp__test_server__*",
            "--permission-prompt-tool", "stdio",
            "--dangerously-skip-permissions",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        async def drain_stderr():
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break

        stderr_task = asyncio.create_task(drain_stderr())

        async def send(obj: dict) -> None:
            proc.stdin.write((json.dumps(obj) + "\n").encode())
            await proc.stdin.drain()

        async def read_one(timeout: float = 10.0) -> dict | None:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
                if line:
                    text = line.decode().strip()
                    if text:
                        return json.loads(text)
                return None
            except asyncio.TimeoutError:
                return None

        try:
            # Step 1: Send InitializeRequest
            await send({
                "type": "control_request",
                "request": {
                    "subtype": "initialize",
                    "request_id": "init_1",
                    "hooks": {},
                    "sdk_mcp_servers": ["test_server"],
                },
            })
            init_response = await read_one(10)
            assert init_response is not None, "No response to InitializeRequest"
            assert init_response.get("type") == "control_response"

            # Step 2: Send McpSetServers
            await send({
                "type": "control_request",
                "request": {
                    "subtype": "mcp_set_servers",
                    "request_id": "mcp_set_1",
                    "servers": {
                        "test_server": {"type": "sdk", "name": "test_server"},
                    },
                },
            })
            set_response = await read_one(10)
            assert set_response is not None, "No response to McpSetServers"
            assert set_response.get("type") == "control_response"

            # Step 3: Handle MCP handshake messages
            handshake_methods_seen = []
            for _ in range(20):
                event = await read_one(10)
                if event is None:
                    break
                req = event.get("request", {})
                msg = req.get("message", {})
                method = msg.get("method", "")
                request_id = event.get("request_id") or req.get("request_id", "")
                rpc_id = msg.get("id")

                handshake_methods_seen.append(method)

                if method == "initialize":
                    await send(_mcp_resp(request_id, rpc_id, {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "test_server", "version": "1.0.0"},
                    }))
                elif method == "notifications/initialized":
                    await send({
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request_id,
                            "response": {
                                "mcp_response": {
                                    "jsonrpc": "2.0",
                                    "result": {},
                                    "id": rpc_id or 0,
                                },
                            },
                        },
                    })
                elif method == "tools/list":
                    await send(_mcp_resp(request_id, rpc_id, {"tools": TOOLS}))
                    break

            assert "initialize" in handshake_methods_seen, (
                f"Expected 'initialize' in handshake, got: {handshake_methods_seen}"
            )
            assert "tools/list" in handshake_methods_seen, (
                f"Expected 'tools/list' in handshake, got: {handshake_methods_seen}"
            )

            await asyncio.sleep(0.5)

            # Step 4: Send user message asking to use the greet tool
            await send({
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "Use the greet tool to greet Alice. Just call the tool, nothing else.",
                },
                "parent_tool_use_id": None,
                "session_id": "",
            })

            # Step 5: Event loop -- handle events until Result
            tool_called = False
            result_received = False
            for _ in range(40):
                event = await read_one(30)
                if event is None:
                    break

                etype = event.get("type", "")

                if etype == "control_request":
                    req = event.get("request", {})
                    msg = req.get("message", {})
                    sub = req.get("subtype", "")
                    method = msg.get("method", "")
                    request_id = event.get("request_id") or req.get("request_id", "")
                    rpc_id = msg.get("id")

                    if sub == "mcp_message" and method == "tools/call":
                        params = msg.get("params", {})
                        name = params.get("name", "")
                        args = params.get("arguments", {})
                        assert name == "greet", f"Expected tool 'greet', got '{name}'"
                        tool_called = True
                        await send(_mcp_resp(request_id, rpc_id, {
                            "content": [{"type": "text", "text": f"Hello, {args.get('name', 'World')}!"}],
                        }))
                    elif sub == "permission":
                        # Auto-allow any permission requests
                        await send({
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": request_id,
                                "response": {
                                    "behavior": "allow",
                                    "updatedInput": req.get("tool_input", {}),
                                },
                            },
                        })

                elif etype == "result":
                    result_received = True
                    assert not event.get("is_error", False), (
                        f"Result was an error: {event.get('result', '')}"
                    )
                    break

            assert tool_called, "The greet tool was never called by the model"
            assert result_received, "No Result event was received"

        finally:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            stderr_task.cancel()
