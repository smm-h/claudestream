# Integration tests against real claude CLI

## Problem
All 73 tests are unit tests with mocked I/O. No tests verify behavior against the actual `claude` binary — session lifecycle, multi-turn conversations, streaming deltas, permission handling, and the CLI commands are untested end-to-end.

## Solution
Add `pytest.mark.integration` tests that spawn real `claude` sessions:
- Single-turn: create AsyncSession, send one prompt, verify SystemInit metadata, receive AssistantText + Result
- Multi-turn: two send() calls on same session, verify second turn has context from first
- Streaming: verify StreamDelta events arrive with partial tokens
- Sync: same tests using SyncSession
- CLI: use strictcli's `app.test()` to test send, stream, events commands end-to-end
- Permission policy: verify allow/deny actually affects tool execution

Mark all with `@pytest.mark.integration` so they're skipped by default. Run with `uv run pytest -m integration`.

## Constraints
- Requires authenticated `claude` CLI on PATH
- Cannot run in CI without auth setup
- Slow (each test spawns a subprocess and waits for API response)
- API costs real money — keep prompts minimal

## Affected files
- `tests/test_integration.py` (new)
- `tests/conftest.py` (add integration marker registration)
- `pyproject.toml` (add pytest marker config)

## Effort
Medium — straightforward to write but needs careful timeout handling and cleanup.
