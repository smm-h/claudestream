# SDK MCP server support (custom tools)

## Context

Claude Code supports custom tools via SDK MCP servers. The controlling process (the parent that spawned the CLI) acts as the MCP server. The protocol works as follows:

- At session start, the parent sends an `InitializeRequest` message via stdin, with `sdk_mcp_servers` listing server names (e.g. `["my_tools"]`)
- When Claude calls a tool from an SDK MCP server, the CLI sends an `McpRequest` (a JSON-RPC `tools/call` message) via stdout
- The parent executes the tool handler and sends back an `McpResponse` via stdin
- Tool names follow the `mcp__server_name__tool_name` convention (double underscores)
- The CLI's `--allowedTools` flag can reference MCP tool names with wildcards (e.g. `mcp__my_tools__*`)

A downstream project needs agents to call custom tools during a claudestream session -- tools like `create_child(goal, tools, scope)` and `tap_out(reason)` that are handled by the host application, not by Claude Code.

## Problem

claudestream already has `InitializeRequest`, `McpRequest`, and `McpResponse` message/event types defined in `messages.py` and `events.py`, but they are not wired up. There is no way for a host application to:

1. Register custom tools with schemas so Claude knows they exist
2. Receive tool calls routed through the SDK MCP protocol
3. Send tool results back to the session
4. Use a convenient decorator-based API for simple tools

## Proposed solution

### Decorator API (schema auto-generated from type hints)

```python
session = AsyncSession(work_dir="/path/to/project")

@session.tool("my_server")
async def create_child(goal: str, tools: list[str], scope: str) -> str:
    """Delegate work to a sub-agent."""
    child_id = await spawn_child(goal, tools, scope)
    return f"Child {child_id} started"

@session.tool("my_server")
async def tap_out(reason: str) -> str:
    """Signal that the agent cannot complete its task."""
    record_failure(reason)
    return "Acknowledged"
```

The `@session.tool` decorator introspects the function's type hints and docstring to auto-generate the JSON Schema for the tool's `inputSchema`. The server name groups tools under one SDK MCP server.

### Explicit schema + handler registration

```python
session.register_tool(
    server="my_server",
    name="create_child",
    input_schema={
        "type": "object",
        "properties": {
            "goal": {"type": "string"},
            "tools": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string"},
        },
        "required": ["goal", "tools", "scope"],
    },
    handler=handle_create_child,
)
```

### Implementation details

- On session start, claudestream sends an `InitializeRequest` with `sdk_mcp_servers` listing all registered server names
- The session event loop watches for `McpRequest` events, dispatches to the registered handler, and sends an `McpResponse` back via stdin
- Registered tool names are automatically added to `--allowedTools` (using the `mcp__server__*` wildcard per server)
- Both sync and async handlers are supported

### External MCP servers (separate concern)

Support for `--mcp-config <path>` (pointing to an MCP config JSON file for external MCP servers managed by the CLI itself) is a separate feature and not covered here.

## Affected files

- `claudestream/messages.py` -- `InitializeRequest`, `McpResponse` already defined; may need refinement
- `claudestream/events.py` -- `McpRequest` event already defined; may need refinement
- `claudestream/_async_session.py` -- initialize handshake, tool registration API, `@tool` decorator, MCP request dispatching
- `claudestream/_sync_session.py` -- sync wrappers for tool registration
- `claudestream/_process.py` -- `ProcessConfig.build_argv` to include `--allowedTools` for registered MCP tools

## Effort

Medium. The message types exist but the initialize handshake, tool registration API, decorator with schema generation, and MCP request/response routing all need implementation and testing.
