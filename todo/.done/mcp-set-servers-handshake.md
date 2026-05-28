# SDK MCP tool registration: missing mcp_set_servers step

## Problem

Custom tools registered via `@tool` are never visible to the model. The SDK MCP handshake is incomplete.

## Root cause

The initialization flow sends `InitializeRequest` with `sdk_mcp_servers: ["server_name"]`, but this only **declares** server names to the CLI. It does NOT trigger the `tools/list` handshake or make tools available.

A second control request (`mcp_set_servers`) is required after initialization to actually connect the SDK MCP servers:

```json
{
    "type": "control_request",
    "request": {
        "subtype": "mcp_set_servers",
        "request_id": "mcp_set_1",
        "servers": {
            "server_name": {
                "type": "sdk",
                "name": "server_name"
            }
        }
    }
}
```

Only after this request does the CLI:
1. Register the SDK MCP server transport
2. Send `tools/list` McpRequest back to the host
3. Make the tools visible to the model

## Evidence

- Binary analysis of Claude Code v2.1.153 confirms `setMcpServers` sends `mcp_set_servers` as a separate step after init
- Live test: session with custom tools shows `tools=32` (builtins only), `mcp_servers=[]`, no `McpRequest` events received
- The `tools/list` and `tools/call` handlers in `_handle_mcp_request` are correct — they're just never triggered

## Secondary issue

The init `control_response` (sent by CLI after InitializeRequest) is parsed as `UnknownEvent` because `parse_event()` has no handler for `type="control_response"`. Should be parsed to avoid warning logs and capture available commands/models/agents.

## Fix

In `_async_session.py`, after sending `InitializeRequest` and receiving the init response, send a `mcp_set_servers` control request for each SDK MCP server. This requires:

1. New message type in `messages.py`: `McpSetServers`
2. Send it in `_start()` after the InitializeRequest
3. Parse `control_response` in `_protocol.py`

## Impact

All custom tool registration via `@tool` decorator is broken. The `tools` parameter on `SessionConfig` and `tool_handlers` on `invoke_agent` have no effect. Consumers fall back to text-based workarounds.

## Consumer

gamehome (Dijkstra executor) needs `create_child`, `tap_out`, and `write_feedback` as real tool calls, replacing fragile JSON-in-text delegation parsing.
