# readline() in _read_turn has no fatal timeout — hangs forever

## Problem

`_read_turn` in `_async_session.py` reads events via `read_events(stdout)` which calls `stream.readline()` with no timeout. If the Claude CLI subprocess stops producing output (API rate limit, overload, stuck initialization, network issue), `readline()` blocks the entire asyncio event loop for that session forever.

The health check (`_health_timeout`, default 30s) only logs a warning — it does not abort, kill the subprocess, or raise an error.

## Impact

When `invoke_agent` is used inside an MCP tool handler (like shopkeep's `spawn_agent`), the inner session's infinite readline blocks the outer session's MCP response. The entire pipeline hangs with no recovery.

## Observed in

shopkeep orchestrator — after 5-7 successful sub-agent invocations, a later spawn hangs for 36+ hours. The subprocess is alive but not producing output.

## Fix

Make the health check fatal: after N seconds (configurable, default 300s?), kill the subprocess and raise `ClaudeStreamError("Session health timeout")`. Or wrap `readline()` in `asyncio.wait_for()` with a per-line timeout.
