"""Permission policy system for Claude Code tool requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable


@dataclass
class Allow:
    """Allow the tool to execute."""
    updated_input: dict | None = None


@dataclass
class Deny:
    """Deny the tool execution."""
    message: str = "Denied by policy"


Decision = Allow | Deny


@runtime_checkable
class Policy(Protocol):
    """Protocol for permission policies."""

    def decide(self, tool_name: str, tool_input: dict) -> Decision | None:
        """Decide whether to allow a tool call.

        Returns:
            Allow or Deny to auto-resolve.
            None to surface the request to the consumer.
        """
        ...


# ---------------------------------------------------------------------------
# Built-in policies
# ---------------------------------------------------------------------------


class AllowAllPolicy:
    """Auto-allow every tool call."""

    def decide(self, tool_name: str, tool_input: dict) -> Decision:
        return Allow()


class DenyAllPolicy:
    """Auto-deny every tool call."""

    def decide(self, tool_name: str, tool_input: dict) -> Decision:
        return Deny("Denied by policy: all tools denied")


# Claude Code's built-in tools
BUILTIN_TOOLS = frozenset({
    "Task", "Bash", "Edit", "Read", "Write",
    "MultiEdit", "Glob", "Grep", "LS",
    "TodoRead", "TodoWrite",
    "WebFetch", "WebSearch",
    "NotebookRead", "NotebookEdit",
})


class AllowBuiltinsPolicy:
    """Allow Claude Code's built-in tools, surface others to consumer."""

    def decide(self, tool_name: str, tool_input: dict) -> Decision | None:
        if tool_name in BUILTIN_TOOLS:
            return Allow()
        return None


class AllowListPolicy:
    """Allow only explicitly listed tools."""

    def __init__(self, tools: list[str]):
        self._tools = frozenset(tools)

    def decide(self, tool_name: str, tool_input: dict) -> Decision | None:
        if tool_name in self._tools:
            return Allow()
        return Deny(f"Denied by policy: {tool_name} not in allowlist")


class CallbackPolicy:
    """Delegate decisions to a user-provided callback."""

    def __init__(self, fn: Callable[[str, dict], Decision | None]):
        self._fn = fn

    def decide(self, tool_name: str, tool_input: dict) -> Decision | None:
        return self._fn(tool_name, tool_input)


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def allow_all() -> AllowAllPolicy:
    """Create a policy that auto-allows all tools."""
    return AllowAllPolicy()


def deny_all() -> DenyAllPolicy:
    """Create a policy that auto-denies all tools."""
    return DenyAllPolicy()


def allow_builtins() -> AllowBuiltinsPolicy:
    """Create a policy that allows Claude Code's built-in tools."""
    return AllowBuiltinsPolicy()


def allow_list(tools: list[str]) -> AllowListPolicy:
    """Create a policy that allows only the listed tools."""
    return AllowListPolicy(tools)


def callback(fn: Callable[[str, dict], Decision | None]) -> CallbackPolicy:
    """Create a policy from a callback function."""
    return CallbackPolicy(fn)


# ---------------------------------------------------------------------------
# CLI flags conversion
# ---------------------------------------------------------------------------


def policy_to_flags(policy: Policy | None) -> list[str]:
    """Convert a policy to CLI flags for Claude Code.

    Static policies (allow_all, allowlist) become --permission-mode/--allowedTools flags.
    Callback policies require --permission-prompt-tool stdio for interactive control.
    None means no permission flags (use defaults).
    """
    if policy is None:
        return []
    if isinstance(policy, AllowAllPolicy):
        return ["--dangerously-skip-permissions"]
    if isinstance(policy, DenyAllPolicy):
        return ["--permission-mode", "dontAsk"]
    if isinstance(policy, AllowListPolicy):
        flags = ["--permission-prompt-tool", "stdio"]
        if policy._tools:
            flags.extend(["--allowedTools", ",".join(sorted(policy._tools))])
        return flags
    # CallbackPolicy, AllowBuiltinsPolicy, or any custom Policy
    return ["--permission-prompt-tool", "stdio"]
