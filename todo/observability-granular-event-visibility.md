# Granular event visibility for invoke_agent sessions

## Problem

When using `invoke_agent` to run multi-turn agent sessions (e.g., a crawler that explores a site, a code generator that writes a connector), the caller has very limited visibility into what the agent is doing. The event stream provides typed events but their contents are opaque — the caller must manually inspect each event type and extract data.

## What's visible today

- Event types: `SystemInit`, `AssistantMessage`, `ToolUse`, `ToolResult`, `McpRequest`, `Result`, `UnknownEvent`
- `AssistantMessage.content` contains `TextBlock` objects with the full text (but callers typically truncate to a preview)
- `Result.total_cost_usd`, `Result.usage` for cost/token tracking
- `ToolUse` has `tool_name` but extracting `tool_input` requires parsing the content blocks

## What's not easily accessible

- **Tool call arguments**: Which URL is being navigated to? What file is being read? The arguments are buried in `AssistantMessage.content` as `ToolUseBlock` objects, not surfaced on `ToolUse` events after flattening.
- **Tool results**: What did the tool return? `ToolResult` after flattening contains the result text but the caller has to know to look for it and parse it.
- **MCP message payloads**: `McpRequest` events have a `raw` dict but no typed fields for method, params, or result. The caller sees "an MCP thing happened" but not what.
- **File operations**: Which files did Claude read/write? `FileWrite`/`FileEdit` events exist but there's no aggregate "files modified this session" view (the session tracks `_files_modified` internally but doesn't expose it).
- **Structured progress**: No concept of "the agent is on step 3 of 7" or "the agent has visited 12/20 pages." The caller must parse free text to infer progress.

## What would help

### 1. Richer flattened events

After `flatten_event`, the caller should get events with all relevant data directly accessible:

- `ToolUse` should include `tool_input: dict` (the arguments), not just `tool_name`
- `ToolResult` should include `tool_name` and a summary of the result
- `McpRequest` should be flattened to include `method`, `tool_name`, `arguments`, and the response

### 2. Session-level observability

- `session.files_modified` — set of file paths Claude has read/written (already tracked internally as `_files_modified`, just needs to be public)
- `session.tool_call_count` — how many tool calls have been made
- `session.tool_call_history` — list of `(tool_name, arguments_summary, result_summary, duration_ms)` tuples
- `session.turn_summaries` — per-turn: text length, tool calls made, cost

### 3. Callback hooks for real-time monitoring

In addition to the existing `on_turn_complete` / `on_error` hooks:

- `on_tool_call(tool_name, arguments)` — fired before tool execution
- `on_tool_result(tool_name, result_summary, duration_ms)` — fired after
- `on_mcp_request(method, tool_name, arguments)` — fired for MCP dispatches
- `on_file_modified(path, operation)` — fired on Write/Edit

These would let callers build dashboards, progress bars, or audit logs without parsing the raw event stream.

### 4. Structured logging integration

An opt-in mode where the session emits structlog events for every tool call, result, and turn — with full arguments and results, not truncated previews. The caller just configures structlog handlers and gets complete visibility for free.

## Observed in

shopkeep's crawl and connector generation pipelines. The pipeline logs `assistant_text preview=` with 100-char truncation and `event type=McpRequest` with no payload. Debugging failures (e.g., "why did cross_check fail?") requires reading trace files on disk after the session ends, not real-time visibility during the session.
