# Dijkstra integration needs

## Context

Dijkstra (AI-driven game generation) uses claudestream as its LLM invocation layer. After building the full pipeline and attempting the first game generation, here are all the needs we've discovered.

## Already filed (reference only)

- `custom-tool-registration.md` -- register create_child/tap_out as custom tools
- `pre-execution-tool-interception.md` -- intercept Write/Edit before execution for scope enforcement

## New needs

### 1. Profile resolution as a first-class feature

Currently Dijkstra has to manually resolve Claude profile → environment variables:
- Read `~/.claudelauncher/tokens.json` for `CLAUDE_CODE_OAUTH_TOKEN`
- Set `CLAUDE_CONFIG_DIR` to `~/.claude-<profile>/`

This should be a claudestream feature: `AsyncSession(profile="personal")` that handles the env resolution internally. The resolution logic lives in claudewheel (`launch.py:resolve_launch_config`), but claudestream shouldn't depend on claudewheel. Either duplicate the resolution or extract it to a shared utility.

### 2. Extended thinking visibility

When the agent enters extended thinking (which can last 5-10 minutes for complex tasks), the event stream goes silent. No events are emitted until thinking completes and the agent starts responding. This creates a "is it still alive?" problem.

Options:
- Emit periodic heartbeat events while no other events arrive
- Emit thinking content as it's generated (if Claude CLI exposes it)
- At minimum: expose whether the underlying subprocess is still alive

### 3. Session cancellation

No way to gracefully cancel a running session. If the agent thinks for too long, the only option is to kill the subprocess. claudestream should provide `session.cancel()` that:
- Sends SIGTERM to the Claude CLI process
- Emits a cancellation event
- Cleans up resources

### 4. Retry and error handling for API errors

What happens when the Claude API returns 429 (rate limit) or 500 (server error)? Does Claude CLI handle retries internally? If not, claudestream should:
- Detect rate limit responses
- Implement exponential backoff
- Emit retry events so the host knows what's happening
- Distinguish transient errors (retry) from permanent errors (surface to host)

### 5. File write tracking abstraction

Dijkstra tracks file writes by inspecting ToolUse events for Write/Edit tool names and parsing their input dicts. This is fragile -- it depends on Claude Code's internal tool input schema. If Claude Code changes how Write's input is structured, our parsing breaks silently.

claudestream should provide a higher-level abstraction:
- `FileWriteEvent(path, content)` emitted when a Write tool succeeds
- `FileEditEvent(path, old, new)` for edits
- These are derived from the raw ToolUse/ToolResult events but normalized

### 6. Full session transcript access

For logging and debugging, Dijkstra needs the full session transcript (all messages, tool calls, tool results, thinking). Currently we accumulate AssistantText events and count ToolUse events, but we don't have the full conversation.

`session.transcript` or a `TranscriptEvent` that provides the complete conversation history would help. Or: an option to write the raw NDJSON stream to a file for replay.

### 7. Token usage from Result event

Dijkstra needs token counts (input_tokens, output_tokens) from each session. The Result event has a `usage` field. Verify this is reliably populated and document the exact field paths. If extended thinking tokens are reported separately, expose that too.

### 8. Subprocess health check

A method to check if the Claude CLI subprocess is still alive without sending a message. Useful for the executor to detect crashed sessions without waiting for timeout.

`session.is_alive() -> bool`

## Priority

For the MVP game generation pipeline to work reliably:
- **P0**: Profile resolution (#1) -- without this, auth doesn't work across profiles
- **P0**: Session cancellation (#3) -- without this, hung agents block the whole pipeline
- **P1**: Retry/error handling (#4) -- transient API errors shouldn't kill game generation
- **P1**: Token usage (#7) -- cost tracking depends on this
- **P2**: Extended thinking visibility (#2) -- nice for UX, not blocking
- **P2**: File write tracking (#5) -- current approach works, just fragile
- **P2**: Session transcript (#6) -- helpful for debugging
- **P3**: Health check (#8) -- convenience

## Effort

Items 1, 3, 7, 8 are small (hours). Items 2, 4 are medium (days). Items 5, 6 are medium (need to understand Claude CLI's event format deeply).
