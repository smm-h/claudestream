"""Typed message structs for all Claude Code stream input messages, including user prompts, tool results, and permission responses."""

from __future__ import annotations

from typing import Any

import msgspec

__all__ = [
    "UserMessage",
    "AllowPermission",
    "DenyPermission",
    "McpResponse",
    "McpSetServers",
    "InitializeRequest",
    "ControlRequest",
]


class UserMessage(msgspec.Struct, frozen=True):
    """A user prompt sent to Claude Code via stdin."""

    content: str | list[Any]  # Prompt text or structured content blocks
    parent_tool_use_id: str | None = None  # ID of the parent tool_use that triggered this message; None at top level
    session_id: str = ""  # Session ID to route this message to

    def to_dict(self) -> dict:
        msg: dict = {"role": "user", "content": self.content}
        return {
            "type": "user",
            "message": msg,
            "parent_tool_use_id": self.parent_tool_use_id,
            "session_id": self.session_id,
        }


class AllowPermission(msgspec.Struct, frozen=True):
    """Allow a permission request."""

    request_id: str  # ID of the PermissionRequest being allowed
    updated_input: dict  # Optionally modified tool input to use instead of the original

    def to_dict(self) -> dict:
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": self.request_id,
                "response": {
                    "behavior": "allow",
                    "updatedInput": self.updated_input,
                },
            },
        }


class DenyPermission(msgspec.Struct, frozen=True):
    """Deny a permission request."""

    request_id: str  # ID of the PermissionRequest being denied
    message: str  # Reason for denial shown to the model

    def to_dict(self) -> dict:
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": self.request_id,
                "response": {
                    "behavior": "deny",
                    "message": self.message,
                },
            },
        }


class McpResponse(msgspec.Struct, frozen=True):
    """Response to an MCP tool call request."""

    request_id: str  # ID of the McpRequest being responded to
    mcp_response: dict  # MCP protocol response payload

    def to_dict(self) -> dict:
        return {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": self.request_id,
                "response": {
                    "mcp_response": self.mcp_response,
                },
            },
        }


class McpSetServers(msgspec.Struct, frozen=True):
    """Register SDK MCP servers with the Claude Code CLI."""

    request_id: str  # Unique ID for this control request
    servers: dict  # Server name to server config mapping (e.g. {"name": {"type": "sdk", "name": "name"}})

    def to_dict(self) -> dict:
        return {
            "type": "control_request",
            "request": {
                "subtype": "mcp_set_servers",
                "request_id": self.request_id,
                "servers": self.servers,
            },
        }


class InitializeRequest(msgspec.Struct, frozen=True):
    """SDK initialization request sent at session start."""

    request_id: str = "init_1"  # Unique ID for this initialization request
    hooks: dict = {}  # Hook configuration mapping hook names to handlers
    sdk_mcp_servers: list[str] = []  # MCP server names to connect at session start

    def to_dict(self) -> dict:
        return {
            "type": "control_request",
            "request": {
                "subtype": "initialize",
                "request_id": self.request_id,
                "hooks": self.hooks,
                "sdk_mcp_servers": self.sdk_mcp_servers,
            },
        }


class ControlRequest(msgspec.Struct, frozen=True):
    """A generic control request sent to the Claude Code CLI (interrupt, set_model, etc.)."""

    request_id: str  # Unique ID correlating this request to its control_response
    subtype: str  # Control request subtype (e.g. "interrupt", "set_model")
    payload: dict = {}  # Subtype-specific fields merged into the inner request object

    def to_dict(self) -> dict:
        # request_id is placed BOTH at the top level and nested inside `request`.
        # InitializeRequest/McpSetServers (which provably work against the real CLI)
        # nest it under `request`; the SDK envelope carries it top-level. Emitting
        # both satisfies every observed CLI form.
        return {
            "type": "control_request",
            "request_id": self.request_id,
            "request": {
                "subtype": self.subtype,
                "request_id": self.request_id,
                **self.payload,
            },
        }
