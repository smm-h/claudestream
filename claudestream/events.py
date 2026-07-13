"""Typed event dataclasses for every Claude Code stream output event, including assistant messages, tool use, permissions, and results."""

from __future__ import annotations

from typing import Any

import msgspec

__all__ = [
    "Event",
    "SystemInit",
    "ApiRetry",
    "CompactBoundary",
    "TextBlock",
    "ToolUseBlock",
    "ThinkingBlock",
    "ToolResultBlock",
    # ContentBlock is a type alias (not renderable by selfdoc ref),
    # so it is excluded from __all__. Import it by name if needed.
    "Usage",
    "ContextCategory",
    "ContextUsage",
    "AssistantMessage",
    "ToolResultMessage",
    "AssistantText",
    "ToolUse",
    "Thinking",
    "ToolResult",
    "FileWrite",
    "FileEdit",
    "StreamDelta",
    "Result",
    "RateLimit",
    "PermissionRequest",
    "McpRequest",
    "HookEvent",
    "UnknownEvent",
    "ControlResponse",
    "BudgetThreshold",
    "AskResult",
]


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Event(msgspec.Struct, frozen=True):
    """Base class for all stream events."""

    type: str  # Event type discriminator (e.g. "assistant", "tool_use", "result")
    session_id: str | None = None  # Claude Code session ID; None if not yet assigned
    uuid: str | None = None  # Unique identifier for this event


# ---------------------------------------------------------------------------
# System events
# ---------------------------------------------------------------------------


class SystemInit(Event, frozen=True):
    """First event in the stream. Contains session metadata."""

    cwd: str = ""  # Working directory of the Claude Code session
    tools: list[str] = []  # Names of available tools
    mcp_servers: list[str] = []  # Names of connected MCP servers
    model: str = ""  # Model identifier (e.g. "claude-sonnet-4-20250514")
    permission_mode: str = ""  # Permission mode (e.g. "default", "plan")
    claude_code_version: str = ""  # Installed Claude Code CLI version


class ApiRetry(Event, frozen=True):
    """Emitted before retrying a failed API call."""

    attempt: int = 0  # Current retry attempt number (1-indexed)
    max_retries: int = 0  # Maximum number of retries configured
    retry_delay_ms: float = 0.0  # Delay before this retry in milliseconds
    error_status: int | None = None  # HTTP status code of the failed request; None if non-HTTP error
    error: str = ""  # Human-readable error message


class CompactBoundary(Event, frozen=True):
    """Emitted when conversation history is compacted."""

    pass


# ---------------------------------------------------------------------------
# Content block types (used inside messages)
# ---------------------------------------------------------------------------


class TextBlock(msgspec.Struct, frozen=True):
    """A text content block from an assistant message."""

    type: str = "text"  # Block discriminator
    text: str = ""  # Text content of the block


class ToolUseBlock(msgspec.Struct, frozen=True):
    """A tool use content block from an assistant message."""

    type: str = "tool_use"  # Block discriminator
    id: str = ""  # Unique tool use ID for correlating with results
    name: str = ""  # Tool name (e.g. "Read", "Edit", "Bash")
    input: dict = {}  # Tool input arguments as key-value pairs


class ThinkingBlock(msgspec.Struct, frozen=True):
    """An extended thinking content block from an assistant message."""

    type: str = "thinking"  # Block discriminator
    thinking: str = ""  # Extended thinking text content


class ToolResultBlock(msgspec.Struct, frozen=True):
    """A tool result content block from a tool result message."""

    type: str = "tool_result"  # Block discriminator
    tool_use_id: str = ""  # ID of the tool_use this result corresponds to
    content: str | list[Any] = ""  # Result payload: plain text or structured content blocks


ContentBlock = TextBlock | ToolUseBlock | ThinkingBlock | ToolResultBlock


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class Usage(msgspec.Struct, frozen=True):
    """Token usage statistics. Semantics depend on context: per-call in AssistantMessage, cumulative across the session in Result."""

    input_tokens: int = 0  # Cumulative input tokens consumed across the session
    output_tokens: int = 0  # Cumulative output tokens generated across the session
    cache_creation_input_tokens: int = 0  # Cumulative input tokens written to the prompt cache across the session
    cache_read_input_tokens: int = 0  # Cumulative input tokens read from the prompt cache across the session


# ---------------------------------------------------------------------------
# Context usage (returned by get_context_usage())
# ---------------------------------------------------------------------------


class ContextCategory(msgspec.Struct, frozen=True):
    """One category of the model's context window (e.g. system prompt, messages, tools)."""

    name: str  # Category label reported by the CLI
    tokens: int  # Tokens attributed to this category


class ContextUsage(msgspec.Struct, frozen=True):
    """Snapshot of the model's context-window usage, returned by get_context_usage()."""

    total_tokens: int  # Tokens currently occupying the context window
    max_tokens: int  # Maximum tokens the context window can hold
    percentage: float = 0.0  # Fraction of the window in use (0.0 to 1.0 or 0-100, per the CLI)
    categories: list[ContextCategory] = []  # Per-category token breakdown
    auto_compact_enabled: bool = False  # Whether the CLI auto-compacts history near the limit
    raw: dict = {}  # Full unmodified response payload for forward compatibility


# ---------------------------------------------------------------------------
# Message-level events
# ---------------------------------------------------------------------------


class AssistantMessage(Event, frozen=True):
    """Complete assistant response with content blocks."""

    content: list[ContentBlock] = []  # Ordered list of text, tool_use, and thinking blocks
    model: str = ""  # Model that generated this response
    stop_reason: str = ""  # Why generation stopped (e.g. "end_turn", "tool_use")
    usage: Usage | None = None  # Token usage for this API call
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level
    error: str | None = None  # Error message if the response failed


class ToolResultMessage(Event, frozen=True):
    """Tool execution results returned to the model."""

    content: list[ToolResultBlock] = []  # Tool result blocks for this message
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level


# ---------------------------------------------------------------------------
# Flattened convenience events (extracted from message content blocks)
# ---------------------------------------------------------------------------


class AssistantText(Event, frozen=True):
    """Single text block from an assistant message."""

    text: str = ""  # Text content of the block
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level


class ToolUse(Event, frozen=True):
    """Single tool call from an assistant message."""

    tool_use_id: str = ""  # Unique tool use ID for correlating with results
    name: str = ""  # Tool name (e.g. "Read", "Edit", "Bash")
    input: dict = {}  # Tool input arguments as key-value pairs
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level


class Thinking(Event, frozen=True):
    """Single thinking block from an assistant message."""

    text: str = ""  # Extended thinking text content
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level


class ToolResult(Event, frozen=True):
    """Single tool result."""

    tool_use_id: str = ""  # ID of the tool_use this result corresponds to
    content: str | list[Any] = ""  # Result payload: plain text or structured content blocks
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level


# ---------------------------------------------------------------------------
# Derived file-tracking events (emitted alongside ToolUse for file-modifying tools)
# ---------------------------------------------------------------------------


class FileWrite(Event, frozen=True):
    """Derived event emitted when a Write tool succeeds. Contains absolute path and content length."""

    path: str = ""  # Absolute path of the written file
    content_length: int = 0  # Length of the written content in characters


class FileEdit(Event, frozen=True):
    """Derived event emitted when an Edit or MultiEdit tool succeeds. Contains absolute path."""

    path: str = ""  # Absolute path of the edited file


# ---------------------------------------------------------------------------
# Streaming, result, rate limit, permission, MCP, unknown
# ---------------------------------------------------------------------------


class StreamDelta(Event, frozen=True):
    """Partial streaming token. Wraps a raw API streaming event."""

    event: dict = {}  # Raw API streaming event payload
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this event; None at top level

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

    subtype: str = ""  # Result subtype (e.g. "success", "error")
    is_error: bool = False  # Whether the turn ended in an error
    duration_ms: float = 0.0  # Total wall-clock time for this turn in milliseconds
    duration_api_ms: float = 0.0  # Time spent in API calls in milliseconds
    num_turns: int = 0  # Number of agentic turns in this interaction
    result: str = ""  # Final text result of the turn
    stop_reason: str = ""  # Why generation stopped (e.g. "end_turn", "tool_use")
    total_cost_usd: float = 0.0  # Cumulative API cost for the session in US dollars
    usage: Usage | None = None  # Cumulative token usage for the session
    api_error_status: int | None = None  # HTTP status code if the turn ended with an API error


class BudgetThreshold(Event, frozen=True):
    """Fired when a budget threshold is crossed. Informational only -- does not stop the session."""

    type: str = "budget_threshold"  # Override base type with a fixed default
    metric: str = ""  # One of "cost", "turns", "tokens"
    threshold: float = 0.0  # The threshold value that was crossed
    current_value: float = 0.0  # The current value that crossed the threshold


class RateLimit(Event, frozen=True):
    """Rate limit status change."""

    status: str = ""  # Rate limit status (e.g. "warning", "exceeded")
    resets_at: int | None = None  # Unix timestamp when the rate limit resets
    rate_limit_type: str = ""  # Type of rate limit (e.g. "token", "request")
    utilization: float = 0.0  # Current utilization as a fraction (0.0 to 1.0)


class PermissionRequest(Event, frozen=True):
    """Permission request from Claude Code. Surfaced when sandbox doesn't auto-resolve."""

    request_id: str = ""  # Unique ID for responding to this permission request
    tool_name: str = ""  # Name of the tool requesting permission
    tool_input: dict = {}  # Input arguments the tool wants to execute
    decision_reason: str = ""  # Explanation of why permission is needed
    tool_use_id: str = ""  # ID of the tool_use block that triggered this request


class McpRequest(Event, frozen=True):
    """MCP tool call request from Claude Code."""

    request_id: str = ""  # Unique ID for responding to this MCP request
    server_name: str = ""  # Name of the MCP server being called
    message: dict = {}  # MCP protocol message payload


class HookEvent(Event, frozen=True):
    """Hook lifecycle event from Claude Code (emitted when include_hook_events is True)."""

    hook_name: str = ""  # Name of the hook that fired (e.g. "PreToolUse")
    hook_data: dict = {}  # Hook-specific data payload


class UnknownEvent(Event, frozen=True):
    """Forward-compatible event for unrecognized event types."""

    raw: dict = {}  # Original unprocessed event data for forward compatibility


class ControlResponse(Event, frozen=True):
    """Response to a control request (e.g. initialize, mcp_set_servers)."""

    request_id: str = ""  # ID of the control request this responds to
    subtype: str = ""  # Response subtype ("success" or "error")
    response: dict = {}  # Success response payload (inner "response" object)
    error: str = ""  # Error text when subtype == "error"


# ---------------------------------------------------------------------------
# Convenience result (returned by ask())
# ---------------------------------------------------------------------------


class AskResult(msgspec.Struct, frozen=True):
    """Complete response from a single ask() call."""

    text: str  # Concatenated assistant text from the response
    usage: Usage | None = None  # Cumulative token usage for the entire ask() call
    cost_usd: float = 0.0  # Total API cost in US dollars
    duration_ms: float = 0.0  # Total wall-clock time in milliseconds
    is_error: bool = False  # Whether the call ended in an error
    num_turns: int = 0  # Number of agentic turns in this interaction
    duration_api_ms: float = 0.0  # Time spent in API calls in milliseconds
    stop_reason: str = ""  # Why generation stopped (e.g. "end_turn", "tool_use")
    result: str = ""  # Final text result from the Result event
    api_error_status: int | None = None  # HTTP status code if the call ended with an API error
    subtype: str = ""  # Result subtype (e.g. "success", "error")
