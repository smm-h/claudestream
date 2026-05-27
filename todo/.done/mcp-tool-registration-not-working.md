# SDK MCP tools not visible to Claude Code model

## Problem

Tools registered via `@tool` decorator and passed to `invoke_agent()` via `tool_handlers` are not visible to the Claude Code model during the session. The `InitializeRequest(sdk_mcp_servers=["server-name"])` is sent, but Claude reports the tools are not in its loaded tool list.

## Reproduction

1. Define tools with `@tool("my-server", inject=["ctx"])` 
2. Create an AgentDefinition with matching ToolSchema entries
3. Call `invoke_agent(definition, config, tool_handlers={...})`
4. Claude's response says: "my tools are: Agent, AskUserQuestion, Bash, Edit, Glob, Grep, Read, ScheduleWakeup, Skill, ToolSearch, Write" — only built-in tools, no MCP tools
5. Claude also checks ToolSearch (deferred tools) and doesn't find them

## Expected behavior

After `InitializeRequest` with `sdk_mcp_servers=["my-server"]`, Claude Code should:
1. Send `tools/list` MCP request to the SDK
2. SDK responds with the registered tool schemas
3. Claude sees the tools as available alongside built-in tools

## Evidence

Downstream project (shopkeep) has 4 browser tools registered as `@tool("shopkeep-crawler", ...)`. The tools are correctly built by `_build_tools()` and the `_tools_by_server` dict is populated. But Claude never sends `McpRequest` events — it simply doesn't see the tools.

Tested with both sonnet and opus models. Sonnet silently falls back to using Bash. Opus stops and reports the tools are missing.

## Questions

1. Is `InitializeRequest` actually being processed by Claude Code? Is there an acknowledgment event?
2. Does the `--dangerously-skip-permissions` flag from `skip_permissions=True` interfere with MCP server registration?
3. Is there a minimum Claude CLI version required for SDK MCP servers to work?
4. Does the Sandbox `tools` allow-list affect MCP tool discovery (not just usage)?
