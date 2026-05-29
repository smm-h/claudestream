# False-positive auth error detection in _async_session.py

## Problem

`_read_turn()` at `_async_session.py:690-705` scans every `AssistantMessage` for substrings like `"not logged in"` and `"invalid authentication"` in the content text. If found, it raises `ClaudeStreamError("Authentication failed...")`.

This produces false positives when Claude's response legitimately contains those phrases — for example, when MCP tool registration fails silently and Claude says something like "I don't have authentication for these tools" or "I'm not logged in to this service." The actual problem is that Claude can't see the registered MCP tools, but the error message blames auth.

## Observed in

shopkeep's `invoke_agent` path with custom MCP tools (navigate, take_screenshot, etc.). The session starts successfully (`SystemInit` event received), then Claude responds with text that matches the heuristic, and the session is killed with a misleading auth error. Meanwhile, `ask()` and other non-MCP paths work fine with the same profile.

## Impact

- Misleading error messages waste debugging time (looks like an auth problem, but auth is fine)
- The heuristic can't distinguish between "Claude Code CLI is not authenticated" and "Claude is talking about authentication in its response"

## Suggested fix

Instead of pattern-matching on assistant message content, detect auth failures from protocol-level signals — e.g., a specific error event type, a non-200 HTTP status from the CLI, or the process exiting with a known error code. If heuristic detection must stay, restrict it to the FIRST assistant message in a session (auth failures happen immediately, not mid-conversation) and require the `error` field to be set (not just content text).
