"""Tests for permission policies."""

from claudestream.policy import (
    Allow, Deny,
    AllowAllPolicy, DenyAllPolicy, AllowBuiltinsPolicy, AllowListPolicy, CallbackPolicy,
    allow_all, deny_all, allow_builtins, allow_list, callback,
    policy_to_flags, BUILTIN_TOOLS,
)


class TestAllowAll:
    def test_always_allows(self):
        p = allow_all()
        assert isinstance(p.decide("Bash", {"command": "rm -rf /"}), Allow)
        assert isinstance(p.decide("CustomTool", {}), Allow)

    def test_flags(self):
        assert policy_to_flags(allow_all()) == ["--dangerously-skip-permissions"]


class TestDenyAll:
    def test_always_denies(self):
        p = deny_all()
        assert isinstance(p.decide("Bash", {}), Deny)
        assert isinstance(p.decide("Read", {}), Deny)

    def test_flags(self):
        assert policy_to_flags(deny_all()) == ["--permission-mode", "dontAsk"]


class TestAllowBuiltins:
    def test_allows_builtins(self):
        p = allow_builtins()
        for tool in ["Bash", "Read", "Write", "Edit", "Task"]:
            assert isinstance(p.decide(tool, {}), Allow), f"{tool} should be allowed"

    def test_surfaces_unknown(self):
        p = allow_builtins()
        assert p.decide("CustomMcpTool", {}) is None

    def test_builtin_tools_complete(self):
        expected = frozenset({
            "Task", "Bash", "Edit", "Read", "Write", "MultiEdit", "Glob", "Grep", "LS",
            "TodoRead", "TodoWrite", "WebFetch", "WebSearch", "NotebookRead", "NotebookEdit",
        })
        assert BUILTIN_TOOLS == expected

    def test_flags(self):
        flags = policy_to_flags(allow_builtins())
        assert "--permission-prompt-tool" in flags
        assert "stdio" in flags


class TestAllowList:
    def test_allows_listed(self):
        p = allow_list(["Read", "Write"])
        assert isinstance(p.decide("Read", {}), Allow)
        assert isinstance(p.decide("Write", {}), Allow)

    def test_denies_unlisted(self):
        p = allow_list(["Read"])
        result = p.decide("Bash", {})
        assert isinstance(result, Deny)
        assert "Bash" in result.message

    def test_flags(self):
        flags = policy_to_flags(allow_list(["Read", "Write"]))
        assert "--permission-prompt-tool" in flags
        assert "--allowedTools" in flags


class TestCallback:
    def test_delegates(self):
        p = callback(lambda name, inp: Allow() if name == "Read" else Deny("no"))
        assert isinstance(p.decide("Read", {}), Allow)
        assert isinstance(p.decide("Bash", {}), Deny)

    def test_can_return_none(self):
        p = callback(lambda name, inp: None)
        assert p.decide("Bash", {}) is None

    def test_flags(self):
        flags = policy_to_flags(callback(lambda n, i: Allow()))
        assert "--permission-prompt-tool" in flags


class TestPolicyToFlags:
    def test_none_policy(self):
        assert policy_to_flags(None) == []

    def test_custom_policy_instance(self):
        """Any Policy-conforming object gets stdio flag."""
        class CustomPolicy:
            def decide(self, tool_name, tool_input):
                return Allow()
        flags = policy_to_flags(CustomPolicy())
        assert "--permission-prompt-tool" in flags
