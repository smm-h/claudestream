"""Sandbox and permission types for Claude Code sessions."""

from __future__ import annotations

import logging
import os

import msgspec

log = logging.getLogger(__name__)


class Allow(msgspec.Struct, frozen=True):
    """Allow the tool to execute."""

    updated_input: dict | None = None


class Deny(msgspec.Struct, frozen=True):
    """Deny the tool execution."""

    message: str = "Denied by policy"


Decision = Allow | Deny


# Claude Code's built-in tools
BUILTIN_TOOLS = frozenset({
    "Task", "Bash", "Edit", "Read", "Write",
    "MultiEdit", "Glob", "Grep", "LS",
    "TodoRead", "TodoWrite",
    "WebFetch", "WebSearch",
    "NotebookRead", "NotebookEdit",
})


class Sandbox(msgspec.Struct, frozen=True):
    """Declarative sandbox configuration for a Claude Code session.

    Controls which tools are available, filesystem scope, and behavior flags.
    """

    tools: list[str] | None = None
    """Tool allow-list. None = all tools allowed."""

    bare: bool = False
    """If True, pass --bare to suppress CLAUDE.md."""

    write_paths: list[str] | None = None
    """Filesystem scope for Write/Edit/MultiEdit. None = no restriction."""

    log_violations: bool = False
    """Log denied tool calls at WARNING level."""

    skip_permissions: bool = False
    """If True, pass --dangerously-skip-permissions to bypass all permission prompts."""


def create_sandbox(
    *,
    tools: list[str] | None = None,
    bare: bool = False,
    write_paths: list[str] | None = None,
    log_violations: bool = False,
    skip_permissions: bool = False,
) -> Sandbox:
    """Create a validated Sandbox configuration.

    Raises:
        ValueError: If any tool name is empty or not a string.
    """
    if tools is not None:
        for i, tool in enumerate(tools):
            if not isinstance(tool, str):
                raise ValueError(f"tools[{i}]: expected str, got {type(tool).__name__}")
            if not tool:
                raise ValueError(f"tools[{i}]: tool name must not be empty")

    return Sandbox(
        tools=tools,
        bare=bare,
        write_paths=write_paths,
        log_violations=log_violations,
        skip_permissions=skip_permissions,
    )


def sandbox_to_flags(sandbox: Sandbox | None) -> list[str]:
    """Convert a Sandbox to CLI flags for Claude Code.

    None means no sandbox flags (use defaults).
    """
    if sandbox is None:
        return []

    flags: list[str] = []

    if sandbox.skip_permissions:
        flags.append("--dangerously-skip-permissions")
        # When skipping all permissions, no other permission flags are needed.
        if sandbox.bare:
            flags.append("--bare")
        return flags

    if sandbox.bare:
        flags.append("--bare")

    if sandbox.tools is not None:
        flags.extend(["--allowedTools", ",".join(sandbox.tools)])

    # Permission interception is needed when we restrict tools or write paths
    if sandbox.tools is not None or sandbox.write_paths is not None:
        flags.extend(["--permission-prompt-tool", "stdio"])

    return flags


# Tools that perform filesystem writes and need scope checking.
_WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})


def _resolve_path(path: str, cwd: str) -> str:
    """Resolve a path to an absolute, symlink-free canonical form."""
    if not os.path.isabs(path):
        path = os.path.join(cwd, path)
    return os.path.realpath(path)


def _is_within(target: str, allowed: str) -> bool:
    """Check if *target* is within *allowed* directory (both must be realpath'd).

    Uses string-prefix comparison with a trailing separator to avoid
    '/src/foo' matching '/src/foobar'.
    """
    # Exact match (the file *is* the allowed path, e.g. an allowed file).
    if target == allowed:
        return True
    # Directory containment: ensure the allowed path ends with sep.
    if not allowed.endswith(os.sep):
        allowed += os.sep
    return target.startswith(allowed)


def sandbox_decide(
    sandbox: Sandbox,
    tool_name: str,
    tool_input: dict,
    cwd: str,
) -> Allow | Deny:
    """Decide whether a tool call is allowed under the given Sandbox.

    The Sandbox is the complete authority -- this always returns Allow or Deny,
    never None.
    """
    # 1. Tool allow-list check.
    if sandbox.tools is not None and tool_name not in sandbox.tools:
        return Deny(message=f"Tool '{tool_name}' not in sandbox allow-list")

    # 2. Write-path scope check.
    if sandbox.write_paths is not None and tool_name in _WRITE_TOOLS:
        file_path = tool_input.get("file_path", "")
        if not file_path:
            msg = "Empty file_path in write tool when write_paths is set"
            if sandbox.log_violations:
                log.warning(msg)
            return Deny(message=msg)

        resolved = _resolve_path(file_path, cwd)
        resolved_allowed = [_resolve_path(p, cwd) for p in sandbox.write_paths]

        if not any(_is_within(resolved, a) for a in resolved_allowed):
            msg = f"Path '{file_path}' outside allowed write scope"
            if sandbox.log_violations:
                log.warning(msg)
            return Deny(message=msg)

    # 3. Default: allow.
    return Allow()
