"""Typed message structs for all Claude Code stream input messages."""

from __future__ import annotations

from typing import Any

import msgspec


class UserMessage(msgspec.Struct, frozen=True):
    """A user prompt sent to Claude Code via stdin."""

    content: str | list[Any]
    parent_tool_use_id: str | None = None
    session_id: str = ""

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

    request_id: str
    updated_input: dict

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

    request_id: str
    message: str

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

    request_id: str
    mcp_response: dict

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

    request_id: str = "init_1"
    hooks: dict = {}
    sdk_mcp_servers: list[str] = []

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
