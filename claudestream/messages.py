"""Typed dataclasses for all Claude Code stream input messages."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserMessage:
    """A user prompt sent to Claude Code via stdin."""

    content: str | list
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


@dataclass
class AllowPermission:
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


@dataclass
class DenyPermission:
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


@dataclass
class McpResponse:
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


@dataclass
class InitializeRequest:
    """SDK initialization request sent at session start."""

    request_id: str = "init_1"
    hooks: dict = field(default_factory=dict)
    sdk_mcp_servers: list[str] = field(default_factory=list)

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
