# Observability: implemented items

Split from `observability-granular-event-visibility.md`. These items were implemented across v0.9.0--v0.12.0.

## ToolUse exposes tool_input

`ToolUse` events include `input: dict` (the tool arguments), surfaced during event flattening. Callers no longer need to parse `AssistantMessage.content` blocks to extract tool call arguments.

## files_modified is public

`AsyncSession.files_modified` and `SyncSession.files_modified` expose the set of file paths Claude has read/written during the session. Previously tracked internally as `_files_modified`.

## total_cost_usd is a session property

`session.total_cost_usd` provides cumulative cost tracking without waiting for the final `Result` event.

## Per-turn cost logging

`_write_cost_log` / `cost_log_path` emit structured per-turn cost data to a log file, enabling post-session cost analysis and billing audits.
