# Observe, don't limit: rethink Budget enforcement

## Problem

`Budget(max_cost_usd, max_turns, max_tokens)` hard-kills agent sessions when limits are hit. `max_turns` and `max_tokens` raise `ClaudeStreamError` in `AsyncSession.send()` before the turn starts. `max_cost_usd` is passed to Claude CLI as `--max-budget-usd` which terminates the subprocess mid-turn.

This is wrong for production agent orchestration. Hard-stopping an agent mid-task:
- Leaves work half-done with no cleanup
- Produces incomplete artifacts (half-written connectors, partial crawls, truncated extractions)
- Forces the caller to detect the error, discard the partial output, and restart from scratch
- Makes cost unpredictable — the caller can't know whether the agent will finish in 49 turns or get killed at 50

## The right model

Agents should run to completion. The orchestration layer should **observe and record**, not enforce:

- **Record** every turn's cost (input_tokens, output_tokens, cache_tokens, model, cost_usd, duration_ms) to structured storage
- **Report** running totals to the caller via events or hooks so the caller can make informed decisions
- **Alert** when thresholds are crossed (e.g., "this session has exceeded $5") via a hook or event, without stopping
- **Let the caller decide** — the orchestrator can choose to not send the next message, but that's a caller-side decision, not a claudestream enforcement

## Proposed changes

1. **Keep Budget as a measurement spec, not an enforcement mechanism.** Budget defines what to track and what thresholds to report on, but never kills the session.

2. **Add a `on_budget_threshold` hook** that fires when a threshold is crossed:
   ```python
   config = SessionConfig(
       budget=Budget(
           warn_cost_usd=5.0,   # fires hook at $5, doesn't stop
           warn_turns=50,        # fires hook at 50 turns, doesn't stop
       ),
       hooks={
           "on_budget_threshold": lambda session, metric, value: log.warn(...)
       }
   )
   ```

3. **Remove `--max-budget-usd` from the CLI flags** passed to Claude Code. Or make it opt-in only when the caller explicitly requests hard enforcement (e.g., `Budget(hard_limit_cost_usd=X)` as a separate field from the measurement thresholds).

4. **Expose running totals** via session properties (already partially done: `session.total_cost`, `session.turn_count`, `session.total_tokens`).

## Why this matters

A downstream project runs agents that generate code, verify it, and self-heal on failure. Hard-stopping an agent at turn 50 when it's on turn 49 of a 52-turn task wastes the entire $8 already spent. The orchestrator needs to observe the cost trajectory and make decisions, not have the rug pulled.

The cost of letting an agent run 3 extra turns ($0.50) is always less than the cost of restarting from scratch ($8+).
