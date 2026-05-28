"""Typed message structs for all Claude Code stream input messages, including user prompts, tool results, and permission responses."""

from __future__ import annotations

from typing import Any

import msgspec

__all__ = [
    "UserMessage",
    "AllowPermission",
    "DenyPermission",
    "McpResponse",
    "InitializeRequest",
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
