"""Option structs for configuring claudestream sessions, covering session resolution, debug, MCP, plugins, stream output, process limits, budget, tool schema, and the unified SessionConfig."""

from __future__ import annotations

import msgspec

from claudestream._tools import Tool
from claudestream.policy import Sandbox

__all__ = [
    "Budget",
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

    name: str | None
    session_id: str | None
    resume_session_id: str | None
    continue_last: bool
    fork: bool


class DebugOptions(msgspec.Struct, frozen=True):
    """Debug configuration."""

    enabled: bool
    filter: str | None
    file: str | None


class McpOptions(msgspec.Struct, frozen=True):
    """External MCP server configuration."""

    config_files: list[str]
    strict: bool


class PluginOptions(msgspec.Struct, frozen=True):
    """Plugin loading configuration."""

    dirs: list[str]
    urls: list[str]


class StreamOptions(msgspec.Struct, frozen=True):
    """Stream output behavior."""

    verbose: bool
    include_partial_messages: bool
    include_hook_events: bool
    replay_user_messages: bool
    exclude_dynamic_prompt_sections: bool


class ProcessLimits(msgspec.Struct, frozen=True):
    """Process-level tuning parameters."""

    buffer_limit: int
    shutdown_timeout: float
    version_check_timeout: float
    health_timeout: float


class Budget(msgspec.Struct, frozen=True):
    """Cost/turn/token limits for a session."""

    max_cost_usd: float | None = None
    max_turns: int | None = None
    max_tokens: int | None = None


class ToolSchema(msgspec.Struct, frozen=True):
    """Tool schema without handler -- for JSON-serializable agent definitions."""

    name: str
    description: str
    input_schema: dict
    server: str


class SessionConfig(msgspec.Struct, frozen=True):
    """Unified configuration object for AsyncSession, SyncSession, print_prompt, and invoke_agent."""

    # Required
    model: str
    profile: str

    # Existing session params (with defaults)
    cwd: str | None = None
    binary: str | None = None
    sandbox: Sandbox | None = None
    system_prompt: str | None = None
    tools: list[Tool] | None = None
    extra_args: list[str] | None = None
    env: dict[str, str] | None = None
    resume_session_id: str | None = None

    # Option struct params
    session_resolution: SessionResolution | None = None
    debug: DebugOptions | None = None
    mcp: McpOptions | None = None
    plugins: PluginOptions | None = None
    stream: StreamOptions | None = None
    process_limits: ProcessLimits | None = None
    budget: Budget | None = None

    # SyncSession tuning
    poll_timeout: float = 1.0
    join_timeout: float = 5.0

    # Flat Claude CLI flag params
    effort: str | None = None
    json_schema: dict | None = None
    fallback_model: str | None = None
    betas: list[str] | None = None
    add_dirs: list[str] | None = None
    builtin_tools: list[str] | None = None
    brief: bool = False
    settings: str | None = None
    setting_sources: str | None = None
    file_specs: list[str] | None = None
    agent_name: str | None = None
    agents_json: str | None = None
    hooks: dict | None = None
    no_persistence: bool = False
    from_pr: str | None = None
