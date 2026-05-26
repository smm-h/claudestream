"""Option structs for configuring claudestream sessions, covering session resolution, debug, MCP, plugins, stream output, and process limits."""

from __future__ import annotations

import msgspec

__all__ = [
    "SessionResolution",
    "DebugOptions",
    "McpOptions",
    "PluginOptions",
    "StreamOptions",
    "ProcessLimits",
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
