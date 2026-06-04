"""Option structs for configuring claudestream sessions, covering session resolution, debug, MCP, plugins, stream output, process limits, budget, tool schema, and the unified SessionConfig."""

from __future__ import annotations

from typing import Any

import msgspec

from claudestream._tools import Tool
from claudestream.policy import Sandbox

__all__ = [
    "Budget",
    "validate_budget",
    "ToolSchema",
    "SessionResolution",
    "DebugOptions",
    "McpOptions",
    "PluginOptions",
    "StreamOptions",
    "ProcessLimits",
    "SessionConfig",
]


class SessionResolution(msgspec.Struct, frozen=True):
    """How to resolve which session to use."""

    name: str | None  # Named session identifier for session management
    session_id: str | None  # Explicit session ID to connect to
    resume_session_id: str | None  # Session ID to resume from where it left off
    continue_last: bool  # Continue the most recent session
    fork: bool  # Create a new session forked from an existing one


class DebugOptions(msgspec.Struct, frozen=True):
    """Debug configuration."""

    enabled: bool  # Enable debug output from Claude Code
    filter: str | None  # Filter pattern to limit which debug messages appear
    file: str | None  # Path to write debug output to instead of stderr


class McpOptions(msgspec.Struct, frozen=True):
    """External MCP server configuration."""

    config_files: list[str]  # Paths to MCP server configuration files
    strict: bool  # Reject unknown MCP server names instead of ignoring them


class PluginOptions(msgspec.Struct, frozen=True):
    """Plugin loading configuration."""

    dirs: list[str]  # Local directory paths to load plugins from
    urls: list[str]  # Remote URLs to load plugins from


class StreamOptions(msgspec.Struct, frozen=True):
    """Stream output behavior."""

    verbose: bool  # Emit verbose protocol output in the event stream
    include_partial_messages: bool  # Stream incremental message fragments as they arrive
    include_hook_events: bool  # Include hook lifecycle events in the stream
    replay_user_messages: bool  # Re-emit prior user messages when resuming a session
    exclude_dynamic_prompt_sections: bool  # Omit dynamic system prompt sections from output


class ProcessLimits(msgspec.Struct, frozen=True):
    """Process-level tuning parameters."""

    buffer_limit: int  # Max bytes for the subprocess stdout/stderr buffer
    shutdown_timeout: float  # Seconds to wait for the subprocess to exit gracefully
    version_check_timeout: float  # Seconds to wait for the Claude CLI version check
    health_timeout: float  # Seconds of silence before warning the subprocess may be stuck


class Budget(msgspec.Struct, frozen=True):
    """Cost/turn/token threshold lists for a session."""

    cost_thresholds: list[float] = []  # USD amounts that trigger BudgetThreshold events
    turn_thresholds: list[int] = []  # Turn counts that trigger BudgetThreshold events
    token_thresholds: list[int] = []  # Token counts that trigger BudgetThreshold events


def validate_budget(budget: Budget) -> None:
    """Raise ValueError if any threshold is negative."""
    for value in budget.cost_thresholds:
        if value < 0:
            raise ValueError(f"cost_thresholds contains negative value: {value}")
    for value in budget.turn_thresholds:
        if value < 0:
            raise ValueError(f"turn_thresholds contains negative value: {value}")
    for value in budget.token_thresholds:
        if value < 0:
            raise ValueError(f"token_thresholds contains negative value: {value}")


class ToolSchema(msgspec.Struct, frozen=True):
    """Tool schema without handler -- for JSON-serializable agent definitions."""

    name: str  # Unique tool identifier used in MCP tool calls
    description: str  # Human-readable summary shown to the model
    input_schema: dict  # JSON Schema defining the tool's input parameters
    server: str  # MCP server name that hosts this tool


class SessionConfig(msgspec.Struct, frozen=True):
    """Unified configuration object for AsyncSession, SyncSession, print_prompt, and invoke_agent."""

    # Required
    model: str  # Claude model identifier (e.g. "claude-sonnet-4-20250514")
    profile: str  # Claude Code profile name (e.g. "work", "personal")

    # Existing session params (with defaults)
    cwd: str | None = None  # Working directory for the Claude Code process; None uses current dir
    binary: str | None = None  # Path to the Claude CLI binary; None uses PATH lookup
    sandbox: Sandbox | None = None  # Tool/filesystem sandbox policy; None means no restrictions
    system_prompt: str | None = None  # Custom system prompt to prepend to the session
    tools: list[Tool] | None = None  # User-defined tools served via MCP to Claude Code
    extra_args: list[str] | None = None  # Additional raw CLI arguments passed to the process
    env: dict[str, str] | None = None  # Extra environment variables for the subprocess
    resume_session_id: str | None = None  # Session ID to resume; None starts a new session

    # Option struct params
    session_resolution: SessionResolution | None = None  # Session lookup/resume/fork strategy
    debug: DebugOptions | None = None  # Debug output configuration
    mcp: McpOptions | None = None  # External MCP server configuration
    plugins: PluginOptions | None = None  # Plugin loading paths and URLs
    stream: StreamOptions | None = None  # Controls which events appear in the output stream
    process_limits: ProcessLimits | None = None  # Subprocess buffer/timeout tuning
    budget: Budget | None = None  # Cost, turn, and token limits for the session

    # SyncSession tuning
    poll_timeout: float = 1.0  # Seconds between event queue polls in SyncSession
    join_timeout: float = 5.0  # Seconds to wait for the background thread on SyncSession close

    # Flat Claude CLI flag params
    effort: str | None = None  # Model reasoning effort level (e.g. "low", "medium", "high")
    json_schema: dict | None = None  # JSON Schema to constrain model output format
    fallback_model: str | None = None  # Model to fall back to if the primary model is unavailable
    betas: list[str] | None = None  # Beta feature flags to enable in the session
    add_dirs: list[str] | None = None  # Additional directories to include in the session context
    builtin_tools: list[str] | None = None  # Built-in tool names to enable (e.g. "computer")
    brief: bool = False  # Produce shorter, more concise model responses
    settings: str | None = None  # Path to a custom settings file
    setting_sources: str | None = None  # Comma-separated setting source override
    file_specs: list[str] | None = None  # Files to attach to the session context
    cost_log_path: str | None = None  # Path to JSONL file for per-turn cost logging; None disables logging
    agent_name: str | None = None  # Built-in agent name to activate in Claude Code
    agents_json: str | None = None  # Path to a custom agents JSON configuration file
    hooks: dict | None = None  # Hook definitions for lifecycle events (e.g. pre-tool-use)
    no_persistence: bool = False  # Disable session persistence so nothing is saved to disk
    from_pr: str | None = None  # GitHub PR identifier to load as session context

    # Tool context injection
    tool_context: Any = None  # Object injected into tool handlers via the inject mechanism
