"""Sandbox and permission types for Claude Code sessions."""

from __future__ import annotations

import msgspec


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


def create_sandbox(
    *,
    tools: list[str] | None = None,
    bare: bool = False,
    write_paths: list[str] | None = None,
    log_violations: bool = False,
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
    )


def sandbox_to_flags(sandbox: Sandbox | None) -> list[str]:
    """Convert a Sandbox to CLI flags for Claude Code.

    None means no sandbox flags (use defaults).
    """
    if sandbox is None:
        return []

    flags: list[str] = []

    if sandbox.bare:
        flags.append("--bare")

    if sandbox.tools is not None:
        flags.extend(["--allowedTools", ",".join(sandbox.tools)])

    # Permission interception is needed when we restrict tools or write paths
    if sandbox.tools is not None or sandbox.write_paths is not None:
        flags.extend(["--permission-prompt-tool", "stdio"])

    return flags
