# Observability: remaining items

Split from `observability-remaining.md` (the `ToolResult.tool_name` item was implemented and the original moved to `todo/.done/`). These items are not yet implemented.

## Richer flattened events

- **McpRequest flattening**: `McpRequest` should be flattened to expose `method`, `tool_name`, `arguments`, and `response` as typed fields instead of a raw dict.

## Session-level observability

- **tool_call_history**: A session-level list of `(tool_name, arguments_summary, result_summary, duration_ms)` tuples recording every tool call with timing data.
- **turn_summaries**: Per-turn aggregates: text length, tool calls made, cost incurred.

## Callback hooks for real-time monitoring

In addition to the existing `on_turn_complete` / `on_error` hooks:

- `on_tool_call(tool_name, arguments)` -- fired before tool execution
- `on_tool_result(tool_name, result_summary, duration_ms)` -- fired after
- `on_mcp_request(method, tool_name, arguments)` -- fired for MCP dispatches
- `on_file_modified(path, operation)` -- fired on Write/Edit

These would let callers build dashboards, progress bars, or audit logs without parsing the raw event stream.
