"""Tests for input message serialization."""

from claudestream.messages import (
    UserMessage, AllowPermission, DenyPermission, McpResponse, McpSetServers, InitializeRequest,
    ControlRequest, DialogCompleted, DialogCancelled,
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

    def test_updated_permissions_omitted_when_none(self):
        """Item 11: no updatedPermissions key when updated_permissions is None."""
        msg = AllowPermission(request_id="perm_1", updated_input={"command": "ls"})
        assert "updatedPermissions" not in msg.to_dict()["response"]["response"]

    def test_updated_permissions_serialized_as_camelcase(self):
        """Item 11: updated_permissions -> updatedPermissions when provided."""
        rules = [{"type": "addRule", "rule": {"toolName": "Bash"}}]
        msg = AllowPermission(
            request_id="perm_1", updated_input={"command": "ls"}, updated_permissions=rules
        )
        inner = msg.to_dict()["response"]["response"]
        assert inner["behavior"] == "allow"
        assert inner["updatedInput"] == {"command": "ls"}
        assert inner["updatedPermissions"] == rules

    def test_empty_updated_permissions_list_is_serialized(self):
        """An explicit empty list is still present (only None omits the key)."""
        msg = AllowPermission(
            request_id="perm_1", updated_input={}, updated_permissions=[]
        )
        assert msg.to_dict()["response"]["response"]["updatedPermissions"] == []


class TestDialogCompleted:
    def test_to_dict(self):
        """Item 9: DialogCompleted wire shape -> behavior 'completed' + result."""
        result = {"questions": [{"answer": "Blue"}]}
        msg = DialogCompleted(request_id="dlg_1", result=result)
        d = msg.to_dict()
        assert d["type"] == "control_response"
        assert d["response"]["subtype"] == "success"
        assert d["response"]["request_id"] == "dlg_1"
        assert d["response"]["response"] == {"behavior": "completed", "result": result}

    def test_result_can_be_any_json_value(self):
        msg = DialogCompleted(request_id="dlg_2", result="just a string")
        assert msg.to_dict()["response"]["response"]["result"] == "just a string"


class TestDialogCancelled:
    def test_to_dict(self):
        """Item 9: DialogCancelled wire shape -> behavior 'cancelled', no result."""
        msg = DialogCancelled(request_id="dlg_1")
        d = msg.to_dict()
        assert d["type"] == "control_response"
        assert d["response"]["subtype"] == "success"
        assert d["response"]["request_id"] == "dlg_1"
        assert d["response"]["response"] == {"behavior": "cancelled"}


class TestInitializeSupportedDialogKinds:
    def test_omitted_when_none(self):
        """Item 10: no supportedDialogKinds key when not declared."""
        d = InitializeRequest().to_dict()
        assert "supportedDialogKinds" not in d["request"]

    def test_serialized_as_camelcase(self):
        """Item 10: supported_dialog_kinds -> supportedDialogKinds in the request."""
        kinds = ["AskUserQuestion", "refusal_fallback_prompt"]
        d = InitializeRequest(supported_dialog_kinds=kinds).to_dict()
        assert d["request"]["subtype"] == "initialize"
        assert d["request"]["supportedDialogKinds"] == kinds

    def test_empty_list_is_serialized(self):
        """An explicit empty list is present (only None omits the key)."""
        d = InitializeRequest(supported_dialog_kinds=[]).to_dict()
        assert d["request"]["supportedDialogKinds"] == []


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


class TestMcpSetServers:
    def test_to_dict(self):
        msg = McpSetServers(
            request_id="mcp_set_1",
            servers={"test_server": {"type": "sdk", "name": "test_server"}},
        )
        d = msg.to_dict()
        assert d["type"] == "control_request"
        assert d["request"]["subtype"] == "mcp_set_servers"
        assert d["request"]["request_id"] == "mcp_set_1"
        assert d["request"]["servers"]["test_server"]["type"] == "sdk"
        assert d["request"]["servers"]["test_server"]["name"] == "test_server"

    def test_multiple_servers(self):
        msg = McpSetServers(
            request_id="mcp_set_2",
            servers={
                "server_a": {"type": "sdk", "name": "server_a"},
                "server_b": {"type": "sdk", "name": "server_b"},
            },
        )
        d = msg.to_dict()
        assert len(d["request"]["servers"]) == 2
        assert "server_a" in d["request"]["servers"]
        assert "server_b" in d["request"]["servers"]


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


class TestControlRequest:
    def test_to_dict_envelope_no_payload(self):
        msg = ControlRequest(request_id="ctrl_1", subtype="interrupt")
        assert msg.to_dict() == {
            "type": "control_request",
            "request_id": "ctrl_1",
            "request": {
                "subtype": "interrupt",
                "request_id": "ctrl_1",
            },
        }

    def test_to_dict_with_payload(self):
        msg = ControlRequest(
            request_id="ctrl_2", subtype="set_model", payload={"model": "sonnet"}
        )
        assert msg.to_dict() == {
            "type": "control_request",
            "request_id": "ctrl_2",
            "request": {
                "subtype": "set_model",
                "request_id": "ctrl_2",
                "model": "sonnet",
            },
        }

    def test_request_id_mirrors_initialize_placement(self):
        # request_id must appear nested under "request" exactly like the
        # InitializeRequest envelope that provably works against the real CLI.
        msg = ControlRequest(request_id="ctrl_3", subtype="get_context_usage")
        d = msg.to_dict()
        assert d["request"]["request_id"] == "ctrl_3"
        assert d["request_id"] == "ctrl_3"
