#!/usr/bin/env python3
"""Live test v8: full MCP handshake with proper auth via profile resolution."""

import asyncio
import json
import os
import sys
from claudewheel.profile import resolve_profile


BINARY = "claude"
PROFILE = "work"
TOOLS = [{
    "name": "greet",
    "description": "Greet someone by name",
    "inputSchema": {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Name to greet"}},
        "required": ["name"],
    },
}]


async def main():
    env = {**os.environ, **resolve_profile(PROFILE)}
    proc = await asyncio.create_subprocess_exec(
        BINARY,
        "--output-format", "stream-json",
        "--input-format", "stream-json",
        "--verbose",
        "--model", "sonnet",
        "--allowedTools", "mcp__test_server__*",
        "--permission-prompt-tool", "stdio",
        "--dangerously-skip-permissions",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stderr_lines = []
    async def drain_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            text = line.decode().strip()
            if text:
                stderr_lines.append(text)
    stderr_task = asyncio.create_task(drain_stderr())

    async def send(obj, label=""):
        if label:
            print(f"\n>>> {label}")
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()

    async def read_one(timeout=5):
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
            if line:
                text = line.decode().strip()
                if text:
                    return json.loads(text)
            return None
        except asyncio.TimeoutError:
            return None

    def mcp_resp(request_id, rpc_id, result):
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": request_id,
                "response": {"mcp_response": {"jsonrpc": "2.0", "id": rpc_id, "result": result}},
            },
        }

    try:
        # Step 1: Init
        await send({
            "type": "control_request",
            "request": {"subtype": "initialize", "request_id": "init_1", "hooks": {}, "sdk_mcp_servers": ["test_server"]},
        }, "Init")
        await read_one(5)

        # Step 2: mcp_set_servers
        await send({
            "type": "control_request",
            "request": {"subtype": "mcp_set_servers", "request_id": "mcp_set_1", "servers": {"test_server": {"type": "sdk", "name": "test_server"}}},
        }, "mcp_set_servers")
        await read_one(5)

        # Step 3: MCP handshake
        print("\n--- MCP handshake ---")
        for i in range(20):
            e = await read_one(5)
            if e is None:
                break
            req = e.get("request", {})
            msg = req.get("message", {})
            method = msg.get("method", "")
            request_id = e.get("request_id", "")
            rpc_id = msg.get("id")
            print(f"  [{i}] {method}")

            if method == "initialize":
                await send(mcp_resp(request_id, rpc_id, {
                    "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                    "serverInfo": {"name": "test_server", "version": "1.0.0"},
                }))
            elif method == "notifications/initialized":
                await send({"type": "control_response", "response": {
                    "subtype": "success", "request_id": request_id,
                    "response": {"mcp_response": {"jsonrpc": "2.0", "result": {}, "id": 0}},
                }})
            elif method == "tools/list":
                await send(mcp_resp(request_id, rpc_id, {"tools": TOOLS}))
                print("  TOOLS REGISTERED")

        await asyncio.sleep(0.5)

        # Step 4: User message
        await send({
            "type": "user",
            "message": {"role": "user", "content": "Use the greet tool to greet Alice. Just call the tool, nothing else."},
            "parent_tool_use_id": None,
            "session_id": "",
        }, "UserMessage")

        # Step 5: Event loop
        print("\n=== Events ===")
        for _ in range(40):
            e = await read_one(30)
            if e is None:
                print("(timeout)")
                break

            etype = e.get("type", "?")

            if etype == "system":
                si = e.get("init", {})
                tools = si.get("tools", [])
                mcp = si.get("mcp_servers", [])
                print(f"  SystemInit: {len(tools)} tools, mcp_servers={mcp}")
                mcp_tools = [t for t in tools if "mcp" in t.lower() or "greet" in t.lower()]
                if mcp_tools:
                    print(f"    MCP tools: {mcp_tools}")

            elif etype == "assistant":
                content = e.get("message", {}).get("content", [])
                for block in content:
                    bt = block.get("type", "")
                    if bt == "text":
                        print(f"  Text: {block.get('text', '')[:80]}")
                    elif bt == "tool_use":
                        print(f"  ToolUse: name={block.get('name')}, id={block.get('id')}, input={block.get('input')}")

            elif etype == "control_request":
                req = e.get("request", {})
                msg = req.get("message", {})
                sub = req.get("subtype", "")
                method = msg.get("method", "")
                request_id = e.get("request_id", "")
                rpc_id = msg.get("id")

                if sub == "mcp_message" and method == "tools/call":
                    params = msg.get("params", {})
                    name = params.get("name", "")
                    args = params.get("arguments", {})
                    print(f"  tools/call: {name}({args})")
                    await send(mcp_resp(request_id, rpc_id, {
                        "content": [{"type": "text", "text": f"Hello, {args.get('name', 'World')}!"}],
                    }), "tools/call response")
                elif sub == "permission":
                    print(f"  Permission: tool={req.get('tool_name')}")
                else:
                    print(f"  {etype}: sub={sub} method={method}")

            elif etype == "user":
                print(f"  ToolResult")

            elif etype == "result":
                cost = e.get("total_cost_usd", 0)
                turns = e.get("num_turns", 0)
                result_text = e.get("result", "")
                print(f"  Result: cost=${cost:.4f}, turns={turns}")
                print(f"    text: {result_text[:200]}")
                break

            elif etype == "stream_event":
                pass

            else:
                print(f"  {etype}")

        if stderr_lines:
            print(f"\nStderr ({len(stderr_lines)}):")
            for line in stderr_lines[-5:]:
                print(f"  {line[:200]}")

    finally:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()
        stderr_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
