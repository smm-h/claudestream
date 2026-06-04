# Observe-not-limit budget redesign: implementation spec

## Problem statement

`Budget(max_cost_usd, max_turns, max_tokens)` hard-kills sessions. `max_turns` and `max_tokens` raise `ClaudeStreamError` pre-turn in `AsyncSession.send()` (lines 506-512 of `_async_session.py`). `max_cost_usd` is passed as `--max-budget-usd` to the subprocess via `ProcessConfig` (line 206 of `_process.py`, mapped at lines 170-172 of `_async_session.py`, flag registry entry at line 105 of `_process.py`). This is wrong for production agents: hard-stopping mid-task wastes all prior spend and leaves work incomplete. The caller, not the library, should decide whether to continue.

## Design decisions

All decisions below were confirmed by the user during the planning session.

### 1. Remove hard limits entirely

Delete `max_cost_usd`, `max_turns`, `max_tokens` from `Budget`. This is a clean break -- pre-stable 0.x project, no backward compatibility needed. No deprecation period, no shims.

### 2. New threshold fields

Budget gets three new list fields:

- `cost_thresholds: list[float]` -- default `[]`
- `turn_thresholds: list[int]` -- default `[]`
- `token_thresholds: list[int]` -- default `[]`

Each is a list of values at which the session emits a `BudgetThreshold` event. Example:

```python
Budget(cost_thresholds=[5.0, 10.0], turn_thresholds=[50])
```

### 3. BudgetThreshold event

New event type in `events.py`. Yielded from `send()` during turn iteration, like any other event. Fields:

- `metric: str` -- one of `"cost"`, `"turns"`, `"tokens"`
- `threshold: float` -- the threshold value that was crossed (float for cost, int-as-float for turns/tokens)
- `current_value: float` -- the current accumulated value at the time of crossing

Composable with `session.on(BudgetThreshold, handler)` for callback-style consumption, using the existing `on()` registration mechanism.

### 4. Fire once per threshold

Each threshold value fires exactly once when first crossed. `cost_thresholds=[5.0, 10.0]` fires twice total: once when cumulative cost first exceeds $5, once when it first exceeds $10. The session tracks which thresholds have already fired in a set per metric.

### 5. session.total_cost_usd accumulator

New property on `AsyncSession` (and `SyncSession` via delegation). Sums `Result.total_cost_usd` across turns, analogous to the existing `session.total_tokens` accumulator (lines 69-70, 280-283, 734-735 of `_async_session.py`).

### 6. Remove --max-budget-usd

Never pass the `--max-budget-usd` flag to the subprocess. The subprocess runs uncapped. Cost observation is client-side only.

### 7. JSONL disk persistence

New optional field on `Budget`: `cost_log_path: str | None = None`. When set, each turn's cost record is appended as a JSONL line after the `Result` event is processed. Each record contains:

- All `Result` fields: `subtype`, `is_error`, `duration_ms`, `duration_api_ms`, `num_turns`, `result` (truncated or omitted for size), `stop_reason`, `total_cost_usd`, `usage` (as dict), `api_error_status`
- Session context: `session_id`, `model`
- Timestamp: ISO 8601 UTC
- Running totals: `cumulative_cost_usd`, `cumulative_tokens`, `turn_number`

File is opened in append mode. Writes are atomic (write line + flush). No file locking -- single-writer assumption (one session per log file).

### 8. Hooks are purely informational

No enforcement, no stop flags, no return values. The `BudgetThreshold` event and `on(BudgetThreshold, handler)` callback are informational. The caller decides whether to send the next message by inspecting `session.total_cost_usd`, `session.turn_count`, `session.total_tokens`, or by reacting to `BudgetThreshold` events in their iteration loop.

## Code changes required

### 1. `claudestream/_options.py` -- Budget struct field changes

**Current code (lines 76-81):**
```python
class Budget(msgspec.Struct, frozen=True):
    """Cost/turn/token limits for a session."""

    max_cost_usd: float | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
```

**New code:**
```python
class Budget(msgspec.Struct, frozen=True):
    """Cost/turn/token observation thresholds for a session."""

    cost_thresholds: list[float] = []
    turn_thresholds: list[int] = []
    token_thresholds: list[int] = []
    cost_log_path: str | None = None
```

Delete `max_cost_usd`, `max_turns`, `max_tokens`. Add `cost_thresholds`, `turn_thresholds`, `token_thresholds`, `cost_log_path`.

### 2. `claudestream/events.py` -- new BudgetThreshold event class

Add after the `Result` class (after line 267):

```python
class BudgetThreshold(Event, frozen=True):
    """Emitted when a budget observation threshold is crossed."""

    metric: str = ""       # "cost", "turns", or "tokens"
    threshold: float = 0.0 # The threshold value that was crossed
    current_value: float = 0.0  # Current accumulated value at time of crossing
```

Add `"BudgetThreshold"` to the `__all__` list (after `"Result"` on line 30).

### 3. `claudestream/_async_session.py` -- multiple changes

#### 3a. Remove pre-turn enforcement in send()

**Delete lines 506-512:**
```python
        # Budget enforcement: check limits before starting a new turn
        budget = self._config.budget
        if budget is not None:
            if budget.max_turns is not None and self._turn_count >= budget.max_turns:
                raise ClaudeStreamError("Budget exceeded: max_turns limit reached")
            if budget.max_tokens is not None and self._total_tokens >= budget.max_tokens:
                raise ClaudeStreamError("Budget exceeded: max_tokens limit reached")
```

Replace with nothing (or a blank line). Budget no longer enforces.

#### 3b. Add cost accumulator and threshold tracking to __init__

Add to `__init__` (after line 71, near `_total_tokens`):

```python
self._total_cost_usd: float = 0.0
self._fired_cost_thresholds: set[float] = set()
self._fired_turn_thresholds: set[int] = set()
self._fired_token_thresholds: set[int] = set()
```

#### 3c. Add total_cost_usd property

Add alongside existing `total_tokens` property (after line 283):

```python
@property
def total_cost_usd(self) -> float:
    return self._total_cost_usd
```

#### 3d. Emit BudgetThreshold events after Result in _read_turn()

In `_read_turn()`, after the Result handling block (lines 731-737), before the `return`, accumulate cost and check thresholds:

```python
if isinstance(event, Result):
    self._last_result = event
    self._turn_count += 1
    if event.usage is not None:
        self._total_tokens += event.usage.input_tokens + event.usage.output_tokens
    self._total_cost_usd += event.total_cost_usd

    # Emit BudgetThreshold events for any newly crossed thresholds
    budget = self._config.budget
    if budget is not None:
        for threshold_event in self._check_thresholds(budget):
            for cb in self._callbacks.get(type(threshold_event), []):
                cb(threshold_event)
            yield threshold_event

    # Write cost log entry
    if budget is not None and budget.cost_log_path is not None:
        self._write_cost_log(event, budget.cost_log_path)

    await self._fire_hooks(self._on_turn_complete, self, event)
    return
```

#### 3e. Add _check_thresholds method

New private method on AsyncSession:

```python
def _check_thresholds(self, budget: Budget) -> list[BudgetThreshold]:
    """Check all threshold lists and return BudgetThreshold events for newly crossed thresholds."""
    events = []
    for threshold in budget.cost_thresholds:
        if threshold not in self._fired_cost_thresholds and self._total_cost_usd >= threshold:
            self._fired_cost_thresholds.add(threshold)
            events.append(BudgetThreshold(
                type="budget_threshold",
                metric="cost",
                threshold=threshold,
                current_value=self._total_cost_usd,
            ))
    for threshold in budget.turn_thresholds:
        if threshold not in self._fired_turn_thresholds and self._turn_count >= threshold:
            self._fired_turn_thresholds.add(threshold)
            events.append(BudgetThreshold(
                type="budget_threshold",
                metric="turns",
                threshold=float(threshold),
                current_value=float(self._turn_count),
            ))
    for threshold in budget.token_thresholds:
        if threshold not in self._fired_token_thresholds and self._total_tokens >= threshold:
            self._fired_token_thresholds.add(threshold)
            events.append(BudgetThreshold(
                type="budget_threshold",
                metric="tokens",
                threshold=float(threshold),
                current_value=float(self._total_tokens),
            ))
    return events
```

#### 3f. Add _write_cost_log method

New private method on AsyncSession:

```python
def _write_cost_log(self, result: Result, path: str) -> None:
    """Append a JSONL cost record to the log file."""
    import json
    from datetime import datetime, timezone

    usage_dict = None
    if result.usage is not None:
        usage_dict = {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cache_creation_input_tokens": result.usage.cache_creation_input_tokens,
            "cache_read_input_tokens": result.usage.cache_read_input_tokens,
        }
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": self._session_id,
        "model": self._model_name,
        "turn_number": self._turn_count,
        "subtype": result.subtype,
        "is_error": result.is_error,
        "duration_ms": result.duration_ms,
        "duration_api_ms": result.duration_api_ms,
        "num_turns": result.num_turns,
        "stop_reason": result.stop_reason,
        "total_cost_usd": result.total_cost_usd,
        "usage": usage_dict,
        "api_error_status": result.api_error_status,
        "cumulative_cost_usd": self._total_cost_usd,
        "cumulative_tokens": self._total_tokens,
    }
    line = json.dumps(record) + "\n"
    with open(path, "a") as f:
        f.write(line)
        f.flush()
```

#### 3g. Remove --max-budget-usd from _build_process_config()

**Delete lines 170-172:**
```python
        max_budget_usd: float | None = None
        if config.budget is not None:
            max_budget_usd = config.budget.max_cost_usd
```

**Delete the max_budget_usd kwarg from the ProcessConfig constructor call (line 247):**
```python
            max_budget_usd=max_budget_usd,
```

#### 3h. Add BudgetThreshold to imports

Add `BudgetThreshold` to the import from `claudestream.events` (line 10-29).

### 4. `claudestream/_sync_session.py` -- add total_cost_usd property delegation

Add alongside the existing `total_tokens` property (after line 130):

```python
@property
def total_cost_usd(self) -> float:
    return self._async_session.total_cost_usd if self._async_session else 0.0
```

### 5. `claudestream/_process.py` -- remove max_budget_usd

**Delete from _FLAG_REGISTRY (line 105):**
```python
    ("max_budget_usd", "--max-budget-usd", "value"),
```

**Delete from ProcessConfig class (line 206):**
```python
    max_budget_usd: float | None = None  # Maximum spend in USD for the session; None means unlimited
```

### 6. `claudestream/__init__.py` -- export BudgetThreshold

Add `BudgetThreshold` to the import from `claudestream.events` and to `__all__`.

### 7. `claudestream/_cli.py` -- update agent info and validate

#### 7a. agent info (lines 452-461)

Replace references to `max_cost_usd`, `max_turns`, `max_tokens` with the new threshold fields:

```python
if agent.budget:
    parts = []
    if agent.budget.cost_thresholds:
        parts.append(f"cost_thresholds={agent.budget.cost_thresholds}")
    if agent.budget.turn_thresholds:
        parts.append(f"turn_thresholds={agent.budget.turn_thresholds}")
    if agent.budget.token_thresholds:
        parts.append(f"token_thresholds={agent.budget.token_thresholds}")
    if agent.budget.cost_log_path:
        parts.append(f"cost_log_path={agent.budget.cost_log_path}")
    if parts:
        print(f"Budget:      {', '.join(parts)}")
```

#### 7b. agent validate (lines 483-493)

Replace non-negative checks with threshold validation:

```python
if agent.budget:
    for val in agent.budget.cost_thresholds:
        if val <= 0:
            print("error: budget.cost_thresholds values must be positive", file=sys.stderr)
            return 1
    for val in agent.budget.turn_thresholds:
        if val <= 0:
            print("error: budget.turn_thresholds values must be positive", file=sys.stderr)
            return 1
    for val in agent.budget.token_thresholds:
        if val <= 0:
            print("error: budget.token_thresholds values must be positive", file=sys.stderr)
            return 1
```

### 8. Tests to add/modify

#### 8a. Rewrite `tests/test_budget.py`

The existing test file tests hard enforcement (`ClaudeStreamError` on limit breach, `--max-budget-usd` in argv). All of these tests become obsolete and must be replaced.

**Delete these test classes:**
- `TestMaxCostInArgv` -- tests `--max-budget-usd` in argv (no longer emitted)
- `TestMaxTurnsEnforced` -- tests `ClaudeStreamError` on `max_turns` breach (no longer raised)
- `TestMaxTokensEnforced` -- tests `ClaudeStreamError` on `max_tokens` breach (no longer raised)
- `TestBudgetWithNoneFields` -- tests `None` handling for old fields (fields no longer exist)

**Keep and adapt:**
- `TestTurnCounterIncrements` -- turn counter still works the same
- `TestTokenAccumulator` -- token accumulator still works the same
- `TestNoBudgetNoLimit` -- still valid (no budget = no limits = no thresholds)

**Add new test classes:**
- `TestCostAccumulator` -- verify `session.total_cost_usd` accumulates across turns
- `TestCostThresholdEvents` -- verify `BudgetThreshold` events are emitted when cost thresholds are crossed, fire exactly once per threshold, contain correct metric/threshold/current_value
- `TestTurnThresholdEvents` -- same for turn thresholds
- `TestTokenThresholdEvents` -- same for token thresholds
- `TestMultipleThresholds` -- verify `[5.0, 10.0]` fires two separate events at the right times
- `TestThresholdFiresOnce` -- verify a crossed threshold does not fire again on subsequent turns
- `TestThresholdCallback` -- verify `session.on(BudgetThreshold, handler)` fires the handler
- `TestNoBudgetNoThresholds` -- verify no `BudgetThreshold` events without budget config
- `TestEmptyThresholdLists` -- verify `Budget()` with empty lists emits nothing
- `TestNoMaxBudgetInArgv` -- verify `--max-budget-usd` is never in argv regardless of budget config
- `TestCostLogWritten` -- verify JSONL log file is written when `cost_log_path` is set, contains expected fields
- `TestCostLogNotWrittenWithoutPath` -- verify no log file without `cost_log_path`

#### 8b. Update `tests/test_options.py` if it references Budget fields

Check for any tests that construct `Budget(max_cost_usd=...)` etc. and update to new fields.

#### 8c. Update `tests/test_process.py` if it references max_budget_usd

Check for any tests that check `ProcessConfig.max_budget_usd` or `--max-budget-usd` in argv.

## Migration guide

This is a breaking change (appropriate for 0.x). Callers must update:

### Budget construction

| Before | After |
|--------|-------|
| `Budget(max_cost_usd=10.0)` | `Budget(cost_thresholds=[10.0])` + caller checks `session.total_cost_usd` |
| `Budget(max_turns=50)` | `Budget(turn_thresholds=[50])` + caller checks `session.turn_count` |
| `Budget(max_tokens=100000)` | `Budget(token_thresholds=[100000])` + caller checks `session.total_tokens` |
| `Budget(max_cost_usd=10.0, max_turns=50)` | `Budget(cost_thresholds=[10.0], turn_thresholds=[50])` |

### Enforcement pattern

Before (library enforces):
```python
config = SessionConfig(model="sonnet", profile="default", budget=Budget(max_turns=50))
with SyncSession(config) as session:
    try:
        for event in session.send("prompt"):
            ...
    except ClaudeStreamError:
        print("budget exceeded")
```

After (caller decides):
```python
config = SessionConfig(
    model="sonnet",
    profile="default",
    budget=Budget(turn_thresholds=[50]),
)
with SyncSession(config) as session:
    for event in session.send("prompt"):
        if isinstance(event, BudgetThreshold):
            print(f"threshold crossed: {event.metric}={event.current_value}")
    # Caller decides whether to send the next message
    if session.turn_count >= 50:
        print("stopping after 50 turns")
```

### New property

`session.total_cost_usd` is available on both `AsyncSession` and `SyncSession` for inspecting cumulative cost without needing threshold events.

### Agent definitions

`.agent.json` files that use `budget` must update field names:
```json
// Before
{"budget": {"max_cost_usd": 5.0, "max_turns": 100}}

// After
{"budget": {"cost_thresholds": [5.0], "turn_thresholds": [100]}}
```

### ClaudeStreamError

`ClaudeStreamError("Budget exceeded: max_turns limit reached")` and `ClaudeStreamError("Budget exceeded: max_tokens limit reached")` are no longer raised. Callers that catch these specific errors can remove those handlers.
