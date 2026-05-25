# `bare` as a first-class constructor parameter

## Problem

The `--bare` flag prevents CLAUDE.md auto-discovery, ensuring agents only receive the system prompt the orchestrator assembles rather than picking up instructions from CLAUDE.md files in the working directory tree. This is critical for orchestrators that control prompt assembly.

Currently consumers must pass it through the generic escape hatch:

```python
session = AsyncSession(model="opus", extra_args=["--bare"], ...)
```

This is non-discoverable -- users have to know `--bare` exists as a CLI flag, know that `extra_args` is the mechanism, and manually construct the list. It's the most common extra_arg orchestrators need and deserves a named parameter.

## Proposed API

```python
session = AsyncSession(
    model="opus",
    profile="work",
    bare=True,  # prevents CLAUDE.md auto-discovery
    system_prompt="...",
)
```

## Implementation

- In `_async_session.py`: add `bare: bool = False` to `AsyncSession.__init__`. If `True`, append `"--bare"` to the args passed to `ProcessConfig`.
- Same for `SyncSession` and `print_prompt`.
- No interaction with `extra_args` -- `bare=True` and `extra_args=["--bare"]` should both work (dedup if both are set, or just let the CLI handle the duplicate).

## Scope

Small, surgical change. Three call sites, one new parameter each.
