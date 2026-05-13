"""Tests for input message serialization."""

from claudestream.messages import (
    UserMessage, AllowPermission, DenyPermission, McpResponse, InitializeRequest,
)


class TestUserMessage:
    def test_to_dict_string_content(self):
        msg = UserMessage(content="hello")
        d = msg.to_dict()
        assert d["type"] == "user"
        assert d["message"]["role"] == "user"
        assert d["message"]["content"] == "hello"
        assert d["parent_tool_use_id"] is None
        assert d["session_id"] == ""

    def test_to_dict_list_content(self):
        msg = UserMessage(content=[{"type": "text", "text": "hello"}], session_id="s1")
        d = msg.to_dict()
        assert d["message"]["content"] == [{"type": "text", "text": "hello"}]
        assert d["session_id"] == "s1"

    def test_to_dict_with_parent(self):
        msg = UserMessage(content="hi", parent_tool_use_id="parent_1")
        d = msg.to_dict()
        assert d["parent_tool_use_id"] == "parent_1"


class TestAllowPermission:
    def test_to_dict(self):
        msg = AllowPermission(request_id="perm_1", updated_input={"command": "ls"})
        d = msg.to_dict()
        assert d["type"] == "control_response"
        assert d["response"]["subtype"] == "success"
        assert d["response"]["request_id"] == "perm_1"
        assert d["response"]["response"]["behavior"] == "allow"
        assert d["response"]["response"]["updatedInput"] == {"command": "ls"}


class TestDenyPermission:
    def test_to_dict(self):
        msg = DenyPermission(request_id="perm_1", message="too dangerous")
        d = msg.to_dict()
        assert d["type"] == "control_response"
        assert d["response"]["response"]["behavior"] == "deny"
        assert d["response"]["response"]["message"] == "too dangerous"


class TestMcpResponse:
    def test_to_dict(self):
        msg = McpResponse(request_id="mcp_1", mcp_response={"jsonrpc": "2.0", "result": {"content": []}})
        d = msg.to_dict()
        assert d["response"]["response"]["mcp_response"]["jsonrpc"] == "2.0"


class TestInitializeRequest:
    def test_to_dict(self):
        msg = InitializeRequest(hooks={"PreToolUse": []}, sdk_mcp_servers=["calc"])
        d = msg.to_dict()
        assert d["type"] == "control_request"
        assert d["request"]["subtype"] == "initialize"
        assert d["request"]["hooks"] == {"PreToolUse": []}
        assert d["request"]["sdk_mcp_servers"] == ["calc"]

    def test_defaults(self):
        msg = InitializeRequest()
        d = msg.to_dict()
        assert d["request"]["request_id"] == "init_1"
        assert d["request"]["hooks"] == {}
        assert d["request"]["sdk_mcp_servers"] == []
