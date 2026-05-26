# First-class file write/edit tracking events

## Context

claudestream consumers (e.g., gamehome/Dijkstra) need to know which files a Claude Code session modified. This information drives downstream logic: running quality gates on changed files, tracking agent output for persistence, enforcing scope constraints.

Currently, consumers do this by manually inspecting `ToolUse` events and parsing their `.input` dicts. For example, from `gamehome/src/dijkstra/agents/invoke.py`:

```python
if event.name in ("Write", "Edit"):
    path = event.input.get("file_path") or event.input.get("path", "")
    if path and path not in result.files_written:
        result.files_written.append(path)
```

This pattern is brittle in several ways:

- It hardcodes tool names. Claude Code already has `MultiEdit` (edits multiple files in one call) which gamehome does not track at all. Future tools (e.g., `Patch`, `Move`) would also be missed.
- It guesses at input dict keys (`file_path`, `path`). If Claude Code renames a key or restructures tool inputs, the parsing silently breaks.
- Every consumer must independently implement and maintain this logic. There is no single source of truth inside claudestream.
- Relative paths are not resolved, so consumers may get inconsistent paths depending on the session's working directory.

## Problem

There is no first-class way for claudestream consumers to track which files a session modified. The library emits raw `ToolUse` events and leaves it to each consumer to reverse-engineer file paths from tool input dicts.

## Proposed solution

### New event types

Add three new event dataclasses in `events.py`, all subclasses of `Event`:

- `FileWrite(path: str, content_length: int)` -- emitted when a file is created or overwritten (from `Write` tool)
- `FileEdit(path: str)` -- emitted when a file is edited in-place (from `Edit` or `MultiEdit` tool)
- `FileDelete(path: str)` -- emitted when a file is deleted (from `Bash` tool calls that use `rm` -- best-effort, not guaranteed)

These are **derived events**, emitted alongside (not instead of) the existing `ToolUse` events. Consumers who want the raw tool data still get it; consumers who want file tracking get clean, typed events.

### Derivation during flattening

In `_protocol.py`, the `flatten_event` function already expands `AssistantMessage` into `ToolUse`, `AssistantText`, and `Thinking` events. The same function would additionally emit `FileWrite`/`FileEdit`/`FileDelete` events when it encounters a `ToolUseBlock` whose name is a file-modifying tool.

The mapping (tool name to derived event) should be defined in one place, making it trivial to update when Claude Code adds new tools:

| Tool name | Derived event | Path source | Notes |
|-----------|--------------|-------------|-------|
| `Write` | `FileWrite` | `input["file_path"]` | `content_length` from `len(input.get("content", ""))` |
| `Edit` | `FileEdit` | `input["file_path"]` | |
| `MultiEdit` | `FileEdit` (one per file) | `input["file_path"]` per edit in the edits array, or top-level `file_path` | MultiEdit may edit multiple files in one call |

`FileDelete` is intentionally out of scope for the first version. Tracking deletions from `Bash` tool calls requires parsing shell commands, which is unreliable. It can be added later if Claude Code introduces a dedicated `Delete` tool.

### Path resolution

Derived events should contain **absolute paths**. The flattener needs access to the session's `cwd` (available from `SystemInit.cwd`). Relative paths in tool inputs are resolved against it using `os.path.join(cwd, path)` then `os.path.normpath`.

This means `flatten_event` either needs the cwd as a parameter, or path resolution happens at a higher layer (in `_async_session.py` during event processing). The latter is cleaner since the session already has the cwd.

### Session accumulator

Add a property on `AsyncSession` (and `SyncSession` by delegation):

```python
@property
def files_modified(self) -> set[str]:
    """All files written or edited during this session (absolute paths, deduplicated)."""
    return set(self._files_modified)
```

The accumulator is populated during `_read_turn` when `FileWrite` or `FileEdit` events are yielded. This gives consumers a simple way to query the full set after a turn completes, without manually collecting events.

### Exports

Add `FileWrite`, `FileEdit`, and `FileDelete` to the public API in `__init__.py` and `__all__`.

## Affected files

- `claudestream/events.py` -- new `FileWrite`, `FileEdit`, `FileDelete` dataclasses
- `claudestream/_protocol.py` -- derive file events in `flatten_event`
- `claudestream/_async_session.py` -- accumulate `files_modified`, pass cwd for path resolution
- `claudestream/_sync_session.py` -- expose `files_modified` property by delegation to `AsyncSession`
- `claudestream/__init__.py` -- export new event types
- `tests/test_events.py` -- test new event dataclasses
- `tests/test_protocol_io.py` or new `tests/test_file_tracking.py` -- test derivation logic, path resolution, MultiEdit handling

## Design considerations

- **Emitted alongside, not instead of:** Existing consumers that match on `ToolUse` are unaffected. The new events are purely additive.
- **MultiEdit complexity:** `MultiEdit` can edit multiple files in a single tool call. The flattener must emit one `FileEdit` per file touched. The exact input schema of `MultiEdit` needs to be verified against Claude Code's current implementation.
- **Bash writes are not tracked:** This is acceptable and should be documented. Consumers who restrict `Bash` tool access (as gamehome does via scope enforcement) already mitigate this. The accumulator's docstring should note the limitation.
- **Forward compatibility:** When Claude Code adds new file-modifying tools, only the tool-name-to-event mapping needs updating -- a one-line change. This is dramatically better than every consumer independently guessing.
- **No breaking changes:** All new. Existing event types, `flatten_event` signature (if cwd is optional with `None` default), and session APIs are unchanged.

## Effort

Small. The core change is a mapping table in `flatten_event` and three simple dataclasses. Most of the work is in tests and handling `MultiEdit`'s input structure correctly.
