# Re-lock claudewheel (stuck at 0.4.5) and adapt to 0.22.0 semantics

## Context

`pyproject.toml` correctly declares `claudewheel` unpinned, but `uv.lock` still pins
`claudewheel==0.4.5` — the lock has not been re-synced in a long time. claudewheel is
now at 0.22.0, which includes a major internal refactor and several contract changes
to `resolve_profile`, the API this project calls (`_async_session.py`, `_cli.py`).

## Problem

The dev/test environment resolves against semantics that no longer exist. Tests may
be green against behavior 18 versions old. Downstream consumers lock claudewheel
independently, so this does not affect them — it affects this project's own
correctness signal.

## Work

1. `uv sync` (or `uv lock --upgrade-package claudewheel`) to pick up the latest
   claudewheel; commit the updated `uv.lock`.
2. Re-run the test suite and adapt to the 0.22.0 contract changes:
   - `resolve_profile` no longer performs any filesystem writes or terminal queries
     (safe on read-only mounts / headless) — the original motivation.
   - A corrupt/unreadable `tokens.json` now RAISES (`claudewheel.tokens.TokenStoreError`)
     instead of silently resolving without a token. Error-handling paths that assumed
     silent degradation need updating.
   - Unknown profiles raise `ValueError` listing available profiles; profiles resolve
     purely from the on-disk layout (persisted options.json metadata is no longer
     consulted).
   - New: `CLAUDEWHEEL_CONFIG_DIR` env var overrides the workspace root (default
     `~/.claudewheel`) — useful for tests and containerized use.
3. Check mocks/fixtures in the test suite that stub `resolve_profile` behavior
   against the old semantics.

## Effort

Small-to-medium: the re-lock is one command; the semantic review of error-handling
call sites and test fixtures is the real work.
