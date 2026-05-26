"""A Python library and CLI for streaming Claude Code's JSON protocol, providing typed events, async/sync sessions, and tool registration."""

from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream._sync_session import SyncSession
from claudestream.events import (
    AskResult,
    AssistantMessage,
    AssistantText,
    ApiRetry,
    CompactBoundary,
    ContentBlock,
    Event,
    FileEdit,
    FileWrite,
    HookEvent,
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
from claudestream.messages import (
    AllowPermission,
    DenyPermission,
    InitializeRequest,
    McpResponse,
    UserMessage,
)
from claudestream._protocol import (
    Writable,
    flatten_event,
    parse_event,
    read_events,
    write_message,
)
from claudestream.policy import (
    Allow,
    BUILTIN_TOOLS,
    Deny,
    Sandbox,
    create_sandbox,
)
from claudestream._tools import Tool, collect_tools, tool
from claudestream._options import (
    Budget,
    DebugOptions,
    McpOptions,
    PluginOptions,
    ProcessLimits,
    SessionConfig,
    SessionResolution,
    StreamOptions,
    ToolSchema,
)
from claudestream._process import ProcessConfig, ProcessManager
from claudestream._agent import (
    AgentDefinition,
    discover_agents,
    invoke_agent,
    invoke_agent_sync,
    load_agent,
    resolve_prompt,
)

__all__ = [
    # Sessions
    "AsyncSession",
    "SyncSession",
    "ClaudeStreamError",
    # Events
    "AskResult",
    "Event",
    "SystemInit",
    "ApiRetry",
    "CompactBoundary",
    "AssistantMessage",
    "AssistantText",
    "ToolResultMessage",
    "ToolUse",
    "ToolResult",
    "FileWrite",
    "FileEdit",
    "Thinking",
    "StreamDelta",
    "Result",
    "RateLimit",
    "PermissionRequest",
    "McpRequest",
    "HookEvent",
    "UnknownEvent",
    # Content blocks
    "TextBlock",
    "ToolUseBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    "ContentBlock",
    "Usage",
    # Messages
    "AllowPermission",
    "DenyPermission",
    "InitializeRequest",
    "McpResponse",
    "UserMessage",
    # Protocol
    "Writable",
    "flatten_event",
    "parse_event",
    "read_events",
    "write_message",
    # Policy
    "Allow",
    "BUILTIN_TOOLS",
    "Deny",
    "Sandbox",
    "create_sandbox",
    # Tools
    "Tool",
    "collect_tools",
    "tool",
    # Options
    "Budget",
    "DebugOptions",
    "McpOptions",
    "PluginOptions",
    "ProcessLimits",
    "SessionConfig",
    "SessionResolution",
    "StreamOptions",
    "ToolSchema",
    # Process
    "ProcessConfig",
    "ProcessManager",
    # Agent definitions
    "AgentDefinition",
    "discover_agents",
    "invoke_agent",
    "invoke_agent_sync",
    "load_agent",
    "resolve_prompt",
    # Convenience
    "print_prompt",
]


def print_prompt(prompt: str, config: SessionConfig) -> str:
    """One-shot convenience: send a prompt and return the full response text.

    Creates a SyncSession, sends one message, collects AssistantText events,
    and returns the concatenated text. For claudewheel integration.
    """
    with SyncSession(config) as session:
        result = session.ask(prompt)
    return result.text
