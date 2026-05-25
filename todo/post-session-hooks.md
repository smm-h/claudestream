# Post-session hooks for cleanup and automation

## Context

claudestream is a library for spawning and controlling Claude Code sessions. Consumers like gamehome's Dijkstra executor need to run actions after an agent session completes -- auto-committing files to git, running linters, cleaning up temp files, logging metrics. Currently this logic is embedded in the consumer's event loop, tangled with event processing code.

In gamehome's `invoke.py`, the `_run_turn` function processes events inline (tracking file writes via `ToolUse` events, capturing token usage from `Result`, parsing structured output from accumulated text). Post-session work like committing files or recording metrics must happen after the `async for event in session.send(goal)` loop ends but before `session.close()`. There is no structured hook point for this -- consumers must manually check for the Result event and run their logic inline or immediately after the iteration loop.

This is a universal pattern. Any non-trivial consumer will need post-session actions: commit files, run linters, upload metrics, clean up temp files, notify external systems. Providing a hook mechanism in claudestream eliminates boilerplate and separates event processing from post-session cleanup.

## Problem

1. **No hook point.** AsyncSession and SyncSession have no mechanism for registering callbacks that run after a turn or session completes. Consumers must structure their own code to detect the Result event and run cleanup at the right time.

2. **Cleanup logic mixes with event processing.** In gamehome's `_run_turn`, file tracking happens inside the event loop (`event.name in ("Write", "Edit")` checks inline with all other event handling). Post-turn cleanup (parsing structured output, logging summaries) happens after the loop. There is no separation between "what to do during the stream" and "what to do after the stream."

3. **Error handling is ad-hoc.** If a consumer's post-session cleanup raises, there is no standard way to handle it -- it just propagates up and may interfere with session teardown. Each consumer must write their own try/except around cleanup.

4. **Multi-turn sessions compound the problem.** A session may involve multiple `send()` calls (multi-turn conversation). Consumers may want hooks after each turn (on each Result) and/or hooks after the session closes. These are distinct hook points.

## Proposed API

### Hook registration

```python
session = AsyncSession(model="opus-4", profile="work", ...)

# Register hooks that run after each turn completes (after Result event)
session.on_turn_complete(auto_commit_hook)
session.on_turn_complete(metrics_hook)

# Register hooks that run when the session closes
session.on_close(cleanup_hook)

# Or via constructor
session = AsyncSession(
    ...,
    on_turn_complete=[auto_commit_hook, metrics_hook],
    on_close=[cleanup_hook],
)
```

### Hook signatures

```python
# Turn-complete hook: receives session + the Result event
async def auto_commit_hook(session: AsyncSession, result: Result) -> None:
    # session exposes accumulated state (e.g., files_modified from file tracking)
    ...

async def metrics_hook(session: AsyncSession, result: Result) -> None:
    if result.usage:
        record_tokens(result.usage.input_tokens, result.usage.output_tokens)

# Close hook: receives session only (no specific Result)
async def cleanup_hook(session: AsyncSession) -> None:
    ...
```

### Sync equivalents

```python
# SyncSession uses sync hooks
def sync_commit_hook(session: SyncSession, result: Result) -> None:
    ...

sync_session.on_turn_complete(sync_commit_hook)
```

### Execution semantics

- **Turn-complete hooks** run after the Result event is yielded to the consumer (so the consumer sees Result before hooks fire) but before `send()` returns / the async iterator is exhausted.
- **Close hooks** run at the start of `session.close()`, before the subprocess is terminated.
- Multiple hooks execute in registration order (FIFO).
- Errors in hooks are logged via the `claudestream` logger but do not propagate -- they must not crash the session or prevent subsequent hooks from running.
- Hooks run in the consumer's context (same event loop for async, same thread for sync) -- they are pure Python, not subprocess interactions.

## Design considerations

### Async vs. sync hooks

AsyncSession should accept async hooks (`async def`). SyncSession should accept sync hooks (`def`). Since SyncSession wraps AsyncSession internally, there are two approaches:

- **Option A: SyncSession wraps async hooks.** SyncSession accepts sync hooks but internally wraps them to run on the event loop thread via `run_coroutine_threadsafe`. This keeps the internal plumbing in AsyncSession only.
- **Option B: Parallel implementations.** Both session types independently manage their hook lists. Simpler but duplicated.
- **Recommendation:** Option A. SyncSession already delegates everything to AsyncSession via `_run_coro`. Add a thin wrapper that converts sync hooks to async.

### on_error hooks

Sessions can fail in several ways: timeout (`asyncio.TimeoutError`), subprocess crash (`ClaudeStreamError`), API errors (rate limit with no retry), cancellation. Consumers may want to run different logic on failure vs. success.

- **Option A: Separate on_error hooks.** `session.on_error(error_hook)` where the hook receives `(session, exception)`.
- **Option B: Single hook with status.** `on_turn_complete` receives a result-or-error wrapper: `TurnOutcome(result: Result | None, error: Exception | None)`.
- **Option C: on_turn_complete only fires on success; errors propagate normally.** Consumers handle errors in their own try/except.
- **Recommendation:** Option A. Separate hooks are cleaner. `on_turn_complete` fires on success, `on_error` fires on failure. If both exist, exactly one fires per turn. This mirrors web framework middleware patterns.

### Interaction with the existing `.on()` callback system

AsyncSession already has `session.on(EventType, handler)` for per-event-type callbacks that fire during iteration. Post-session hooks are a different concern:

- `.on()` fires inline during event streaming, before the event is yielded.
- `on_turn_complete` fires after the full turn is done.
- They compose naturally -- `.on()` for real-time event processing, `on_turn_complete` for post-turn actions.

No naming collision, but the distinction should be documented clearly.

### Interaction with file-write tracking

The dijkstra-integration-needs todo (#5) proposes a `session.files_modified` property (or higher-level `FileWriteEvent`/`FileEditEvent`). Post-session hooks compose naturally with that: hooks can read `session.files_modified` to know what to commit. This todo does not depend on file tracking being implemented -- hooks work with any session state the consumer tracks. But when file tracking lands, hooks become more powerful.

### Hook removal

Should hooks be removable? Probably not for v1. Registration-only is sufficient. If needed later, `on_turn_complete` could return a handle with a `.remove()` method.

### Multi-turn hook timing

For multi-turn sessions (multiple `send()` calls), `on_turn_complete` fires after each turn's Result. This means hooks run between turns, which is the right time for per-turn cleanup (e.g., commit files written in this turn). Close hooks run once at session end.

## Implementation plan

### In `_async_session.py`

1. Add hook storage to `__init__`:
   - `self._on_turn_complete: list[Callable[[AsyncSession, Result], Awaitable[None]]]`
   - `self._on_error: list[Callable[[AsyncSession, Exception], Awaitable[None]]]`
   - `self._on_close: list[Callable[[AsyncSession], Awaitable[None]]]`

2. Add registration methods:
   - `on_turn_complete(hook)` -- appends to the turn-complete list
   - `on_error(hook)` -- appends to the error list
   - `on_close(hook)` -- appends to the close list

3. Add constructor parameters (`on_turn_complete`, `on_error`, `on_close`) that accept lists of hooks.

4. Fire turn-complete hooks in `_read_turn` after the `Result` event is yielded:
   ```python
   if isinstance(event, Result):
       self._last_result = event
       # Fire turn-complete hooks (after yield, so consumer saw Result)
       await self._fire_turn_complete(event)
       return
   ```

5. Fire error hooks when `ClaudeStreamError` or other exceptions occur in `_read_turn` or `send`:
   ```python
   except Exception as exc:
       await self._fire_error(exc)
       raise
   ```

6. Fire close hooks in `close()` before terminating the subprocess:
   ```python
   async def close(self) -> None:
       await self._fire_close()
       await self._process_mgr.close()
   ```

7. Implement `_fire_turn_complete`, `_fire_error`, `_fire_close` as private methods that iterate hooks, call each in try/except, and log errors without re-raising.

### In `_sync_session.py`

1. Add `on_turn_complete(hook)`, `on_error(hook)`, `on_close(hook)` that accept sync callables.

2. Wrap sync hooks into async and delegate to the underlying AsyncSession:
   ```python
   def on_turn_complete(self, hook: Callable) -> None:
       async def _async_wrapper(session, result):
           hook(self, result)  # pass SyncSession, not AsyncSession
       self._async_session.on_turn_complete(_async_wrapper)
   ```

3. Add constructor parameters that pre-register hooks (applied in `__enter__` after AsyncSession creation).

### Hook ordering vs. Result yield timing

The Result event must be yielded to the consumer before turn-complete hooks fire. In the current code, `_read_turn` yields events in a loop and returns after yielding Result. The hooks should fire after the yield but before the return:

```python
for evt in events_to_yield:
    yield evt

if isinstance(event, Result):
    self._last_result = event
    await self._fire_turn_complete(event)  # after yield, before return
    return
```

This ensures the consumer's `async for` loop sees Result, then hooks run, then `send()` returns.

## Affected files

- `claudestream/_async_session.py` -- Hook storage, registration methods, constructor params, firing logic in `_read_turn`, `send`, and `close`
- `claudestream/_sync_session.py` -- Sync hook registration, async wrapper delegation, constructor params
- `claudestream/__init__.py` -- No changes needed (hooks are methods on existing exported classes)
- `tests/` -- New test file for hook execution order, error isolation, sync/async parity

## Effort

Small to medium. The core mechanism (list of callables, fire-and-log pattern) is straightforward. The subtlety is in the yield timing within `_read_turn` (hooks must fire after Result is yielded but before the generator returns) and the sync-to-async wrapper in SyncSession.

Estimated: 2-4 hours implementation + tests.
