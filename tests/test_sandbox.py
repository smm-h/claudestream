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
create_sandbox = _mod.create_sandbox
sandbox_to_flags = _mod.sandbox_to_flags


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
