"""Typed dataclasses for all Claude Code stream output events."""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Event:
    """Base class for all stream events."""

    type: str
    session_id: str | None = None
    uuid: str | None = None


# ---------------------------------------------------------------------------
# System events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemInit(Event):
    """First event in the stream. Contains session metadata."""

    cwd: str = ""
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    model: str = ""
    permission_mode: str = ""
    claude_code_version: str = ""


@dataclass(frozen=True)
class ApiRetry(Event):
    """Emitted before retrying a failed API call."""

    attempt: int = 0
    max_retries: int = 0
    retry_delay_ms: float = 0.0
    error_status: int | None = None
    error: str = ""


@dataclass(frozen=True)
class CompactBoundary(Event):
    """Emitted when conversation history is compacted."""

    pass


# ---------------------------------------------------------------------------
# Content block types (used inside messages)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass(frozen=True)
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ThinkingBlock:
    type: str = "thinking"
    thinking: str = ""


@dataclass(frozen=True)
class ToolResultBlock:
    type: str = "tool_result"
    tool_use_id: str = ""
    content: str | list = ""


ContentBlock = TextBlock | ToolUseBlock | ThinkingBlock | ToolResultBlock


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


# ---------------------------------------------------------------------------
# Message-level events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssistantMessage(Event):
    """Complete assistant response with content blocks."""

    content: list[ContentBlock] = field(default_factory=list)
    model: str = ""
    stop_reason: str = ""
    usage: Usage | None = None
    parent_tool_use_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ToolResultMessage(Event):
    """Tool execution results returned to the model."""

    content: list[ToolResultBlock] = field(default_factory=list)
    parent_tool_use_id: str | None = None


# ---------------------------------------------------------------------------
# Flattened convenience events (extracted from message content blocks)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssistantText(Event):
    """Single text block from an assistant message."""

    text: str = ""
    parent_tool_use_id: str | None = None


@dataclass(frozen=True)
class ToolUse(Event):
    """Single tool call from an assistant message."""

    tool_use_id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)
    parent_tool_use_id: str | None = None


@dataclass(frozen=True)
class Thinking(Event):
    """Single thinking block from an assistant message."""

    text: str = ""
    parent_tool_use_id: str | None = None


@dataclass(frozen=True)
class ToolResult(Event):
    """Single tool result."""

    tool_use_id: str = ""
    content: str | list = ""
    parent_tool_use_id: str | None = None


# ---------------------------------------------------------------------------
# Streaming, result, rate limit, permission, MCP, unknown
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamDelta(Event):
    """Partial streaming token. Wraps a raw API streaming event."""

    event: dict = field(default_factory=dict)
    parent_tool_use_id: str | None = None

    @property
    def delta_type(self) -> str | None:
        """Returns 'text_delta', 'input_json_delta', etc."""
        delta = self.event.get("delta", {})
        return delta.get("type")

    @property
    def text(self) -> str | None:
        """Returns text content for text_delta events."""
        delta = self.event.get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text")
        return None

    @property
    def partial_json(self) -> str | None:
        """Returns partial JSON for input_json_delta events."""
        delta = self.event.get("delta", {})
        if delta.get("type") == "input_json_delta":
            return delta.get("partial_json")
        return None

    @property
    def event_type(self) -> str | None:
        """Returns the streaming event type (message_start, content_block_delta, etc.)."""
        return self.event.get("type")


@dataclass(frozen=True)
class Result(Event):
    """Final event in a turn. Contains cost and usage summary."""

    subtype: str = ""
    is_error: bool = False
    duration_ms: float = 0.0
    duration_api_ms: float = 0.0
    num_turns: int = 0
    result: str = ""
    stop_reason: str = ""
    total_cost_usd: float = 0.0
    usage: Usage | None = None
    api_error_status: int | None = None


@dataclass(frozen=True)
class RateLimit(Event):
    """Rate limit status change."""

    status: str = ""
    resets_at: int | None = None
    rate_limit_type: str = ""
    utilization: float = 0.0


@dataclass(frozen=True)
class PermissionRequest(Event):
    """Permission request from Claude Code. Surfaced when policy doesn't auto-resolve."""

    request_id: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    decision_reason: str = ""
    tool_use_id: str = ""


@dataclass(frozen=True)
class McpRequest(Event):
    """MCP tool call request from Claude Code."""

    request_id: str = ""
    server_name: str = ""
    message: dict = field(default_factory=dict)


@dataclass(frozen=True)
class UnknownEvent(Event):
    """Forward-compatible event for unrecognized event types."""

    raw: dict = field(default_factory=dict)
