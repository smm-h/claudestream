"""A Python library and CLI for streaming Claude Code's JSON protocol."""

from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream._sync_session import SyncSession
from claudestream.events import (
    AssistantMessage,
    AssistantText,
    ApiRetry,
    CompactBoundary,
    ContentBlock,
    Event,
    McpRequest,
    PermissionRequest,
    RateLimit,
    Result,
    StreamDelta,
    SystemInit,
    TextBlock,
    Thinking,
    ThinkingBlock,
    ToolResult,
    ToolResultBlock,
    ToolResultMessage,
    ToolUse,
    ToolUseBlock,
    UnknownEvent,
    Usage,
)
from claudestream.policy import (
    Allow,
    AllowAllPolicy,
    AllowBuiltinsPolicy,
    AllowListPolicy,
    CallbackPolicy,
    Deny,
    Policy,
    allow_all,
    allow_builtins,
    allow_list,
    callback,
    deny_all,
)

__all__ = [
    # Sessions
    "AsyncSession",
    "SyncSession",
    "ClaudeStreamError",
    # Events
    "Event",
    "SystemInit",
    "ApiRetry",
    "CompactBoundary",
    "AssistantMessage",
    "AssistantText",
    "ToolResultMessage",
    "ToolUse",
    "ToolResult",
    "Thinking",
    "StreamDelta",
    "Result",
    "RateLimit",
    "PermissionRequest",
    "McpRequest",
    "UnknownEvent",
    # Content blocks
    "TextBlock",
    "ToolUseBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ContentBlock",
    "Usage",
    # Policy
    "Policy",
    "Allow",
    "Deny",
    "AllowAllPolicy",
    "AllowBuiltinsPolicy",
    "AllowListPolicy",
    "CallbackPolicy",
    "allow_all",
    "deny_all",
    "allow_builtins",
    "allow_list",
    "callback",
    # Convenience
    "print_prompt",
]


def print_prompt(
    prompt: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    binary: str | None = None,
    policy: Policy | None = None,
    system_prompt: str | None = None,
    extra_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """One-shot convenience: send a prompt and return the full response text.

    Creates a SyncSession, sends one message, collects AssistantText events,
    and returns the concatenated text. For claudewheel integration.
    """
    parts: list[str] = []
    with SyncSession(
        model=model,
        cwd=cwd,
        binary=binary,
        policy=policy,
        system_prompt=system_prompt,
        extra_args=extra_args,
        env=env,
    ) as session:
        for event in session.send(prompt):
            if isinstance(event, AssistantText):
                parts.append(event.text)
    return "".join(parts)
