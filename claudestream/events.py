"""Typed event classes for all Claude Code stream output events."""

from __future__ import annotations

from typing import Any

import msgspec


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Event(msgspec.Struct, frozen=True):
    """Base class for all stream events."""

    type: str
    session_id: str | None = None
    uuid: str | None = None


# ---------------------------------------------------------------------------
# System events
# ---------------------------------------------------------------------------


class SystemInit(Event, frozen=True):
    """First event in the stream. Contains session metadata."""

    cwd: str = ""
    tools: list[str] = []
    mcp_servers: list[str] = []
    model: str = ""
    permission_mode: str = ""
    claude_code_version: str = ""


class ApiRetry(Event, frozen=True):
    """Emitted before retrying a failed API call."""

    attempt: int = 0
    max_retries: int = 0
    retry_delay_ms: float = 0.0
    error_status: int | None = None
    error: str = ""


class CompactBoundary(Event, frozen=True):
    """Emitted when conversation history is compacted."""

    pass


# ---------------------------------------------------------------------------
# Content block types (used inside messages)
# ---------------------------------------------------------------------------


class TextBlock(msgspec.Struct, frozen=True):
    """A text content block from an assistant message."""

    type: str = "text"
    text: str = ""


class ToolUseBlock(msgspec.Struct, frozen=True):
    """A tool use content block from an assistant message."""

    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict = {}


class ThinkingBlock(msgspec.Struct, frozen=True):
    """An extended thinking content block from an assistant message."""

    type: str = "thinking"
    thinking: str = ""


class ToolResultBlock(msgspec.Struct, frozen=True):
    """A tool result content block from a tool result message."""

    type: str = "tool_result"
    tool_use_id: str = ""
    content: str | list[Any] = ""


ContentBlock = TextBlock | ToolUseBlock | ThinkingBlock | ToolResultBlock


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class Usage(msgspec.Struct, frozen=True):
    """Token usage statistics for an API call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


# ---------------------------------------------------------------------------
# Message-level events
# ---------------------------------------------------------------------------


class AssistantMessage(Event, frozen=True):
    """Complete assistant response with content blocks."""

    content: list[ContentBlock] = []
    model: str = ""
    stop_reason: str = ""
    usage: Usage | None = None
    parent_tool_use_id: str | None = None
    error: str | None = None


class ToolResultMessage(Event, frozen=True):
    """Tool execution results returned to the model."""

    content: list[ToolResultBlock] = []
    parent_tool_use_id: str | None = None


# ---------------------------------------------------------------------------
# Flattened convenience events (extracted from message content blocks)
# ---------------------------------------------------------------------------


class AssistantText(Event, frozen=True):
    """Single text block from an assistant message."""

    text: str = ""
    parent_tool_use_id: str | None = None


class ToolUse(Event, frozen=True):
    """Single tool call from an assistant message."""

    tool_use_id: str = ""
    name: str = ""
    input: dict = {}
    parent_tool_use_id: str | None = None


class Thinking(Event, frozen=True):
    """Single thinking block from an assistant message."""

    text: str = ""
    parent_tool_use_id: str | None = None


class ToolResult(Event, frozen=True):
    """Single tool result."""

    tool_use_id: str = ""
    content: str | list[Any] = ""
    parent_tool_use_id: str | None = None


# ---------------------------------------------------------------------------
# Streaming, result, rate limit, permission, MCP, unknown
# ---------------------------------------------------------------------------


class StreamDelta(Event, frozen=True):
    """Partial streaming token. Wraps a raw API streaming event."""

    event: dict = {}
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


class Result(Event, frozen=True):
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


class RateLimit(Event, frozen=True):
    """Rate limit status change."""

    status: str = ""
    resets_at: int | None = None
    rate_limit_type: str = ""
    utilization: float = 0.0


class PermissionRequest(Event, frozen=True):
    """Permission request from Claude Code. Surfaced when sandbox doesn't auto-resolve."""

    request_id: str = ""
    tool_name: str = ""
    tool_input: dict = {}
    decision_reason: str = ""
    tool_use_id: str = ""


class McpRequest(Event, frozen=True):
    """MCP tool call request from Claude Code."""

    request_id: str = ""
    server_name: str = ""
    message: dict = {}


class UnknownEvent(Event, frozen=True):
    """Forward-compatible event for unrecognized event types."""

    raw: dict = {}


# ---------------------------------------------------------------------------
# Convenience result (returned by ask())
# ---------------------------------------------------------------------------


class AskResult(msgspec.Struct, frozen=True):
    """Complete response from a single ask() call."""

    text: str
    usage: Usage | None = None
    cost_usd: float = 0.0
    duration_ms: float = 0.0
    is_error: bool = False
