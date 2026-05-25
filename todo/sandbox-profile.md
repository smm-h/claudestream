# Sandbox profile for restricted agent sessions

## Problem

Orchestrators that spawn agent sessions (e.g., gamehome/Dijkstra) need to restrict capabilities in multiple dimensions simultaneously:

- **Which tools are available.** An agent writing game code needs Write, Edit, Read, Bash -- not WebSearch or TodoWrite. Currently this requires `policy=allow_list([...])`.
- **Whether CLAUDE.md files are auto-discovered.** Spawned agents should not inherit the orchestrator's CLAUDE.md instructions. Currently this requires `extra_args=["--bare"]` (an undocumented leak-through to Claude Code CLI flags).
- **Which directories agents can write to.** An agent assigned to `src/` should not write to `tests/` or `.git/`. Currently this requires building a custom `CallbackPolicy` that inspects Write/Edit tool inputs and checks paths -- boilerplate that every orchestrator would duplicate.
- **What happens when a restricted action is attempted.** Silent drops make debugging impossible. Currently there is no built-in logging or reporting for denied tool calls.

These are separate concerns today, requiring the caller to manually assemble `policy=`, `extra_args=`, and custom callback code, then hope nothing leaks through the gaps. The result is fragile, verbose, and error-prone.

Real-world example from gamehome (`agents/invoke.py`):

```python
async with AsyncSession(
    effective_model,
    profile,
    system_prompt=system_prompt,
    cwd=str(working_dir),
    policy=allow_all(),       # unrestricted -- no sandboxing at all
    extra_args=extra_args,
)
```

The orchestrator uses `allow_all()` because assembling the correct restrictions is too cumbersome. Every agent gets full access to every tool and every directory.

## Proposed API

A single `create_sandbox()` call that combines all restriction dimensions:

```python
from claudestream import create_sandbox

sandbox = create_sandbox(
    tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob", "LS"],
    bare=True,
    allowed_write_paths=["src/", "tests/"],
    log_violations=True,
)

session = AsyncSession(
    model="opus",
    profile="work",
    sandbox=sandbox,
    system_prompt="...",
)
```

### Parameters

- **`tools: list[str] | None`** -- Tool allow-list. Only these tools are permitted. If `None` (default), all tools are available (backward compat). Maps to `--allowedTools` CLI flag plus policy enforcement.
- **`bare: bool`** -- If `True`, pass `--bare` to Claude Code CLI to suppress CLAUDE.md auto-discovery. Default `False`.
- **`allowed_write_paths: list[str] | None`** -- Filesystem scope enforcement. Write/Edit/MultiEdit calls targeting paths outside this list are denied. Paths are resolved relative to `cwd`. If `None` (default), no scope enforcement.
- **`log_violations: bool`** -- If `True`, log denied tool calls at WARNING level with the tool name, target path, and denial reason. If `False`, deny silently. Default `False`.

### What it replaces

| Before | After |
|--------|-------|
| `policy=allow_list(["Read", "Write", ...])` | `sandbox=create_sandbox(tools=[...])` |
| `extra_args=["--bare"]` | `sandbox=create_sandbox(bare=True)` |
| Custom `CallbackPolicy` for path checking | `sandbox=create_sandbox(allowed_write_paths=[...])` |
| Manual combination of all three | Single `create_sandbox(...)` call |

### Relationship with `policy`

`sandbox` replaces `policy` -- it is a higher-level API that internally creates the right `Policy` implementation. Passing both `sandbox=` and `policy=` should raise `ValueError` (ambiguous configuration). The `policy=` parameter stays for backward compatibility and for callers who need custom decision logic that sandbox does not cover.

## Scope enforcement details

When `allowed_write_paths` is set, the sandbox creates an internal policy that intercepts `PermissionRequest` events for Write, Edit, and MultiEdit tools:

1. Extract the target path from `tool_input` (the `file_path` key for Write/Edit, iterate entries for MultiEdit).
2. Resolve the path relative to the session's `cwd` (using `os.path.realpath` to handle `..` traversal attacks).
3. Check whether the resolved path starts with any of the resolved `allowed_write_paths` prefixes.
4. If allowed: return `Allow()`.
5. If denied: return `Deny(f"Path outside allowed scope: {path}")`. If `log_violations` is set, also log at WARNING level.

Read, Bash, and other tools are unaffected by `allowed_write_paths` -- they pass through the tool allow-list check only.

### Edge cases

- **Bash tool and file writes.** Bash can write files via shell commands (`echo > file`, `cp`, `mv`). `allowed_write_paths` cannot intercept these -- it only covers Write/Edit/MultiEdit. If Bash is in the tool allow-list, the agent can bypass scope enforcement. Orchestrators that need strict scope enforcement should either exclude Bash from the tool list or accept this limitation. Document this clearly.
- **Symlink traversal.** `os.path.realpath` resolves symlinks, preventing `src/../../etc/passwd` style escapes.
- **Absolute vs. relative paths.** Agents may pass absolute or relative paths. Both must be resolved against `cwd` before prefix-checking.

## Implementation plan

### 1. `Sandbox` dataclass and `create_sandbox` factory (in `policy.py`)

```python
@dataclass(frozen=True)
class Sandbox:
    tools: list[str] | None = None
    bare: bool = False
    allowed_write_paths: list[str] | None = None
    log_violations: bool = False
```

`create_sandbox(...)` returns a `Sandbox` instance. The factory exists for forward compatibility (validation, normalization) but initially is just a constructor.

### 2. `SandboxPolicy` (in `policy.py`)

A new `Policy` implementation that combines tool allow-listing with path scope enforcement:

```python
class SandboxPolicy:
    def __init__(self, sandbox: Sandbox, cwd: str):
        ...

    def decide(self, tool_name: str, tool_input: dict) -> Decision | None:
        # 1. Check tool allow-list
        # 2. If Write/Edit/MultiEdit, check allowed_write_paths
        # 3. Log violations if configured
```

`SandboxPolicy` needs `cwd` at construction time to resolve relative paths. This means the session passes `cwd` when constructing the policy internally.

### 3. `AsyncSession` and `SyncSession` changes

Add `sandbox: Sandbox | None = None` parameter. When set:

- Validate that `policy` is not also set (raise `ValueError`).
- Construct a `SandboxPolicy` internally using the sandbox config and `cwd`.
- If `sandbox.bare` is `True`, append `--bare` to the CLI args.
- If `sandbox.tools` is set, pass them as `--allowedTools` in addition to policy enforcement (belt and suspenders -- CLI-level restriction plus policy-level).

### 4. Export from `__init__.py`

Add `Sandbox`, `create_sandbox` to `__all__` and imports.

### 5. `policy_to_flags` update

Handle `SandboxPolicy` to emit the correct CLI flags (`--permission-prompt-tool stdio`, `--allowedTools`, `--bare`).

## Affected files

- `claudestream/policy.py` -- `Sandbox` dataclass, `create_sandbox` factory, `SandboxPolicy` class, `policy_to_flags` update
- `claudestream/_async_session.py` -- New `sandbox` parameter, validation, policy construction
- `claudestream/_sync_session.py` -- Same `sandbox` parameter (forwarded to `AsyncSession`)
- `claudestream/__init__.py` -- Export `Sandbox`, `create_sandbox`
- `tests/test_policy.py` -- Tests for `SandboxPolicy`, `create_sandbox`, path enforcement logic

## Relationship to existing todos

- **`pre-execution-tool-interception.md`** -- That todo describes the general mechanism (intercept tool calls before execution). This todo is a specific, higher-level consumer of that mechanism. If pre-execution interception lands first, `SandboxPolicy` can use it. If not, `SandboxPolicy` works via the existing `PermissionRequest` flow (which already intercepts before execution via `--permission-prompt-tool stdio`).
- **`custom-tool-registration.md`** -- Orthogonal. Custom tools and sandbox restrictions can coexist. A session might have custom tools (create_child, tap_out) and a sandbox (restricted Write paths). The sandbox's tool allow-list would need to include custom tool names.
- **`dijkstra-integration-needs.md`** -- This addresses the "scope enforcement" aspect of Dijkstra's needs. Profile resolution (#1) and other items remain separate.

## Standalone `bare` parameter

For callers who want `--bare` without a full sandbox, also add `bare: bool = False` directly to `AsyncSession`/`SyncSession`. This is a convenience for the common case where orchestrators want to suppress CLAUDE.md but do not need tool or path restrictions:

```python
session = AsyncSession(model="opus", profile="work", bare=True)
```

When both `sandbox.bare=True` and `bare=True` are set, they are equivalent (no conflict). When `sandbox.bare=False` and `bare=True`, `bare=True` wins (union of restrictions).

## Effort

Medium. The core implementation (Sandbox dataclass, SandboxPolicy, session wiring) is straightforward. Path resolution edge cases and test coverage are the bulk of the work. Estimated 1-2 sessions.
