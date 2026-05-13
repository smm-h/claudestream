# MCP server registration

## Problem
Claude Code supports MCP servers for custom tools. claudestream can receive MCP requests (`McpRequest` events) but cannot register MCP servers at session start.

## Solution
Add `mcp_config: str | None` parameter to AsyncSession pointing to an MCP config JSON file. Pass `--mcp-config <path>` to the CLI. For SDK-style MCP (where the controlling process IS the MCP server), implement the initialize handshake with `sdk_mcp_servers` and handle `sdk_control_request` subtype `mcp_message` with responses.

## Affected files
- `claudestream/_async_session.py` (init handshake, MCP request handling)
- `claudestream/_sync_session.py` (pass-through)
- `claudestream/_process.py` (ProcessConfig.build_argv)
- `claudestream/messages.py` (InitializeRequest already exists)

## Effort
Medium — the message types exist, but the handshake flow and MCP response routing need implementation and testing.
