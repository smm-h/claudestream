"""Tests for Sandbox configuration and sandbox_to_flags.

Imports policy.py directly to avoid triggering __init__.py, which still
references old Policy classes removed in this phase. Phase 7 will fix
the package-level imports and this workaround can be removed.
"""

import importlib.util
import sys

import pytest

# Load policy.py directly, bypassing claudestream/__init__.py which still
# imports the removed Policy class from _async_session.py.
_spec = importlib.util.spec_from_file_location(
    "claudestream.policy",
    "claudestream/policy.py",
    submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["claudestream.policy"] = _mod
_spec.loader.exec_module(_mod)

Sandbox = _mod.Sandbox
Allow = _mod.Allow
Deny = _mod.Deny
create_sandbox = _mod.create_sandbox
sandbox_to_flags = _mod.sandbox_to_flags
sandbox_decide = _mod.sandbox_decide


class TestSandboxDefaults:
    def test_default_construction(self):
        s = Sandbox()
        assert s.tools is None
        assert s.bare is False
        assert s.write_paths is None
        assert s.log_violations is False

    def test_all_fields(self):
        s = Sandbox(
            tools=["Read", "Write"],
            bare=True,
            write_paths=["/src"],
            log_violations=True,
        )
        assert s.tools == ["Read", "Write"]
        assert s.bare is True
        assert s.write_paths == ["/src"]
        assert s.log_violations is True

    def test_frozen(self):
        s = Sandbox()
        with pytest.raises(AttributeError):
            s.bare = True  # type: ignore[misc]


class TestCreateSandbox:
    def test_valid_defaults(self):
        s = create_sandbox()
        assert s == Sandbox()

    def test_valid_with_tools(self):
        s = create_sandbox(tools=["Read", "Bash"])
        assert s.tools == ["Read", "Bash"]

    def test_valid_all_fields(self):
        s = create_sandbox(
            tools=["Read"],
            bare=True,
            write_paths=["/src"],
            log_violations=True,
        )
        assert s.tools == ["Read"]
        assert s.bare is True
        assert s.write_paths == ["/src"]
        assert s.log_violations is True

    def test_empty_string_in_tools(self):
        with pytest.raises(ValueError, match="tools\\[1\\]: tool name must not be empty"):
            create_sandbox(tools=["Read", ""])

    def test_non_string_in_tools(self):
        with pytest.raises(ValueError, match="tools\\[0\\]: expected str, got int"):
            create_sandbox(tools=[42])  # type: ignore[list-item]


class TestSandboxToFlags:
    def test_none_returns_empty(self):
        assert sandbox_to_flags(None) == []

    def test_all_defaults_returns_empty(self):
        assert sandbox_to_flags(Sandbox()) == []

    def test_bare_only(self):
        assert sandbox_to_flags(Sandbox(bare=True)) == ["--bare"]

    def test_tools_only(self):
        flags = sandbox_to_flags(Sandbox(tools=["Read", "Write"]))
        assert "--allowedTools" in flags
        idx = flags.index("--allowedTools")
        assert flags[idx + 1] == "Read,Write"
        assert "--permission-prompt-tool" in flags
        assert "stdio" in flags

    def test_write_paths_only(self):
        flags = sandbox_to_flags(Sandbox(write_paths=["/src"]))
        assert flags == ["--permission-prompt-tool", "stdio"]

    def test_combined_flags(self):
        flags = sandbox_to_flags(Sandbox(
            tools=["Read"],
            bare=True,
            write_paths=["/src"],
        ))
        assert "--bare" in flags
        assert "--allowedTools" in flags
        idx = flags.index("--allowedTools")
        assert flags[idx + 1] == "Read"
        assert "--permission-prompt-tool" in flags
        assert "stdio" in flags

    def test_flag_ordering(self):
        """bare comes before allowedTools, permission-prompt-tool comes last."""
        flags = sandbox_to_flags(Sandbox(
            tools=["Read"],
            bare=True,
            write_paths=["/src"],
        ))
        bare_idx = flags.index("--bare")
        tools_idx = flags.index("--allowedTools")
        perm_idx = flags.index("--permission-prompt-tool")
        assert bare_idx < tools_idx < perm_idx


import logging
import os


class TestSandboxDecide:
    """Tests for sandbox_decide permission engine."""

    CWD = "/home/user/project"

    # -- Tool allow-list --

    def test_tool_allowed_by_allowlist(self):
        s = Sandbox(tools=["Read", "Write"])
        result = sandbox_decide(s, "Read", {}, self.CWD)
        assert isinstance(result, Allow)

    def test_tool_denied_by_allowlist(self):
        s = Sandbox(tools=["Read"])
        result = sandbox_decide(s, "Bash", {}, self.CWD)
        assert isinstance(result, Deny)
        assert "Bash" in result.message
        assert "not in sandbox allow-list" in result.message

    def test_tools_none_allows_any(self):
        s = Sandbox(tools=None)
        result = sandbox_decide(s, "Bash", {}, self.CWD)
        assert isinstance(result, Allow)

    # -- Write-path scope (Write tool) --

    def test_write_inside_write_paths(self):
        s = Sandbox(write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/home/user/project/src/main.py"},
            self.CWD,
        )
        assert isinstance(result, Allow)

    def test_write_outside_write_paths(self):
        s = Sandbox(write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/etc/passwd"},
            self.CWD,
        )
        assert isinstance(result, Deny)
        assert "outside allowed write scope" in result.message

    def test_write_relative_path_resolved(self):
        s = Sandbox(write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "src/main.py"},
            "/home/user/project",
        )
        assert isinstance(result, Allow)

    def test_write_dotdot_traversal_denied(self):
        s = Sandbox(write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/home/user/project/src/../../etc/passwd"},
            self.CWD,
        )
        assert isinstance(result, Deny)
        assert "outside allowed write scope" in result.message

    # -- Write-path scope (Edit tool) --

    def test_edit_inside_write_paths(self):
        s = Sandbox(write_paths=["/home/user/project"])
        result = sandbox_decide(
            s, "Edit",
            {"file_path": "/home/user/project/foo.py"},
            self.CWD,
        )
        assert isinstance(result, Allow)

    # -- Write-path scope (MultiEdit tool) --

    def test_multiedit_inside_write_paths(self):
        s = Sandbox(write_paths=["/home/user/project"])
        result = sandbox_decide(
            s, "MultiEdit",
            {"file_path": "/home/user/project/bar.py"},
            self.CWD,
        )
        assert isinstance(result, Allow)

    # -- Non-write tools ignore write_paths --

    def test_read_ignores_write_paths(self):
        s = Sandbox(write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Read",
            {"file_path": "/etc/anything"},
            self.CWD,
        )
        assert isinstance(result, Allow)

    def test_bash_ignores_write_paths(self):
        s = Sandbox(write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Bash",
            {"command": "ls /"},
            self.CWD,
        )
        assert isinstance(result, Allow)

    # -- Empty file_path --

    def test_empty_file_path_denied(self):
        s = Sandbox(write_paths=["/home/user/project"])
        result = sandbox_decide(s, "Write", {"file_path": ""}, self.CWD)
        assert isinstance(result, Deny)
        assert "Empty file_path" in result.message

    def test_missing_file_path_denied(self):
        s = Sandbox(write_paths=["/home/user/project"])
        result = sandbox_decide(s, "Write", {}, self.CWD)
        assert isinstance(result, Deny)
        assert "Empty file_path" in result.message

    # -- Combined checks --

    def test_combined_tool_allowed_and_path_in_scope(self):
        s = Sandbox(tools=["Write"], write_paths=["/home/user/project"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/home/user/project/ok.py"},
            self.CWD,
        )
        assert isinstance(result, Allow)

    def test_combined_tool_allowed_but_path_out_of_scope(self):
        s = Sandbox(tools=["Write"], write_paths=["/home/user/project/src"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/tmp/nope.py"},
            self.CWD,
        )
        assert isinstance(result, Deny)
        assert "outside allowed write scope" in result.message

    # -- Relative write_paths entries --

    def test_relative_write_path_resolved_against_cwd(self):
        s = Sandbox(write_paths=["src"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/home/user/project/src/ok.py"},
            "/home/user/project",
        )
        assert isinstance(result, Allow)

    # -- Path prefix ambiguity --

    def test_path_prefix_no_false_positive(self):
        """'/src/foobar' must NOT match allowed path '/src/foo'."""
        s = Sandbox(write_paths=["/src/foo"])
        result = sandbox_decide(
            s, "Write",
            {"file_path": "/src/foobar/evil.py"},
            self.CWD,
        )
        assert isinstance(result, Deny)

    # -- log_violations --

    def test_log_violations_logs_on_deny(self, caplog):
        s = Sandbox(
            write_paths=["/home/user/project/src"],
            log_violations=True,
        )
        with caplog.at_level(logging.WARNING, logger="claudestream.policy"):
            result = sandbox_decide(
                s, "Write",
                {"file_path": "/etc/passwd"},
                self.CWD,
            )
        assert isinstance(result, Deny)
        assert "outside allowed write scope" in caplog.text

    def test_log_violations_logs_on_empty_path(self, caplog):
        s = Sandbox(
            write_paths=["/home/user/project"],
            log_violations=True,
        )
        with caplog.at_level(logging.WARNING, logger="claudestream.policy"):
            sandbox_decide(s, "Edit", {}, self.CWD)
        assert "Empty file_path" in caplog.text

    def test_log_violations_false_no_log(self, caplog):
        s = Sandbox(
            write_paths=["/home/user/project/src"],
            log_violations=False,
        )
        with caplog.at_level(logging.WARNING, logger="claudestream.policy"):
            sandbox_decide(
                s, "Write",
                {"file_path": "/etc/passwd"},
                self.CWD,
            )
        assert caplog.text == ""
