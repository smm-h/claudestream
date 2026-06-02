# MCP handshake exits after first server's tools/list, causing hang with multi-server agents

## Problem

`_run_mcp_handshake` in `_async_session.py` returns after receiving the FIRST `tools/list` message. When an agent has tools registered across multiple MCP servers (e.g., `shopkeep-crawler` for browser tools and `shopkeep-tools` for bash/spawn_agent), the CLI performs sequential handshakes for each server. The function exits after server A's `tools/list`, but server B's handshake hasn't started yet.

After `_start()` returns, `send()` writes a `UserMessage` to stdin. The CLI then starts server B's handshake, sending `initialize` to stdout and expecting an `McpResponse` on stdin. But it reads the `UserMessage` instead, because it arrived first. The CLI hangs internally — it received a message type it doesn't expect during the handshake sequence.

## Impact

Any agent with tools from 2+ MCP servers will intermittently hang forever on the first `send()` call. The hang is silent — no error, no timeout, just infinite blocking. In shopkeep, this caused 3 crawls to hang for 10+ hours before being manually killed.

## Fix

Track pending servers in `_run_mcp_handshake`. Only return when ALL servers in `_tools_by_server` have sent their `tools/list`:

```python
servers_pending = set(self._tools_by_server.keys())
while servers_pending:
    line = await asyncio.wait_for(...)
    ...
    if method == "tools/list":
        # Determine which server this tools/list belongs to
        servers_pending.discard(server_name)
```

## Observed in

shopkeep's orchestrator pipeline. The orchestrator spawns sub-agents via `spawn_agent` tool. Sub-agents with tools from one server work. Sub-agents with tools from two servers hang.
