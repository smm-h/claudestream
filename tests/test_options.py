"""Tests for option structs: construction, missing fields, and immutability."""

import pytest

from claudestream._options import (
    Budget,
    ToolSchema,
    SessionResolution,
    DebugOptions,
    McpOptions,
    PluginOptions,
    StreamOptions,
    ProcessLimits,
    SessionConfig,
)


class TestSessionResolution:
    def test_construction(self):
        s = SessionResolution(
            name="my-session",
            session_id=None,
            resume_session_id=None,
            continue_last=False,
            fork=False,
        )
        assert s.name == "my-session"
        assert s.session_id is None
        assert s.resume_session_id is None
        assert s.continue_last is False
        assert s.fork is False

    def test_missing_field(self):
        with pytest.raises(TypeError):
            SessionResolution(name="x", session_id=None)  # type: ignore[call-arg]

    def test_frozen(self):
        s = SessionResolution(
            name=None, session_id=None, resume_session_id=None,
            continue_last=False, fork=False,
        )
        with pytest.raises(AttributeError):
            s.name = "changed"  # type: ignore[misc]


class TestDebugOptions:
    def test_construction(self):
        d = DebugOptions(enabled=True, filter="net*", file="/tmp/debug.log")
        assert d.enabled is True
        assert d.filter == "net*"
        assert d.file == "/tmp/debug.log"

    def test_missing_field(self):
        with pytest.raises(TypeError):
            DebugOptions(enabled=True)  # type: ignore[call-arg]

    def test_frozen(self):
        d = DebugOptions(enabled=False, filter=None, file=None)
        with pytest.raises(AttributeError):
            d.enabled = True  # type: ignore[misc]


class TestMcpOptions:
    def test_construction(self):
        m = McpOptions(config_files=["/path/to/mcp.json"], strict=True)
        assert m.config_files == ["/path/to/mcp.json"]
        assert m.strict is True

    def test_missing_field(self):
        with pytest.raises(TypeError):
            McpOptions(config_files=[])  # type: ignore[call-arg]

    def test_frozen(self):
        m = McpOptions(config_files=[], strict=False)
        with pytest.raises(AttributeError):
            m.strict = True  # type: ignore[misc]


class TestPluginOptions:
    def test_construction(self):
        p = PluginOptions(dirs=["/plugins"], urls=["https://example.com/plugin.zip"])
        assert p.dirs == ["/plugins"]
        assert p.urls == ["https://example.com/plugin.zip"]

    def test_missing_field(self):
        with pytest.raises(TypeError):
            PluginOptions(dirs=[])  # type: ignore[call-arg]

    def test_frozen(self):
        p = PluginOptions(dirs=[], urls=[])
        with pytest.raises(AttributeError):
            p.dirs = ["/new"]  # type: ignore[misc]


class TestStreamOptions:
    def test_construction(self):
        s = StreamOptions(
            verbose=True,
            include_partial_messages=False,
            include_hook_events=True,
            replay_user_messages=False,
            exclude_dynamic_prompt_sections=True,
        )
        assert s.verbose is True
        assert s.include_partial_messages is False
        assert s.include_hook_events is True
        assert s.replay_user_messages is False
        assert s.exclude_dynamic_prompt_sections is True

    def test_missing_field(self):
        with pytest.raises(TypeError):
            StreamOptions(verbose=True)  # type: ignore[call-arg]

    def test_frozen(self):
        s = StreamOptions(
            verbose=False, include_partial_messages=False,
            include_hook_events=False, replay_user_messages=False,
            exclude_dynamic_prompt_sections=False,
        )
        with pytest.raises(AttributeError):
            s.verbose = True  # type: ignore[misc]


class TestProcessLimits:
    def test_construction(self):
        p = ProcessLimits(
            buffer_limit=16777216,
            shutdown_timeout=5.0,
            version_check_timeout=2.0,
            health_timeout=30.0,
        )
        assert p.buffer_limit == 16777216
        assert p.shutdown_timeout == 5.0
        assert p.version_check_timeout == 2.0
        assert p.health_timeout == 30.0

    def test_missing_field(self):
        with pytest.raises(TypeError):
            ProcessLimits(buffer_limit=1024)  # type: ignore[call-arg]

    def test_frozen(self):
        p = ProcessLimits(
            buffer_limit=1024, shutdown_timeout=1.0,
            version_check_timeout=1.0, health_timeout=10.0,
        )
        with pytest.raises(AttributeError):
            p.buffer_limit = 2048  # type: ignore[misc]


class TestBudget:
    def test_all_defaults(self):
        b = Budget()
        assert b.max_cost_usd is None
        assert b.max_turns is None
        assert b.max_tokens is None

    def test_construction(self):
        b = Budget(max_cost_usd=1.5, max_turns=10, max_tokens=4096)
        assert b.max_cost_usd == 1.5
        assert b.max_turns == 10
        assert b.max_tokens == 4096

    def test_frozen(self):
        b = Budget()
        with pytest.raises(AttributeError):
            b.max_cost_usd = 2.0  # type: ignore[misc]


class TestToolSchema:
    def test_construction(self):
        t = ToolSchema(name="my_tool", description="Does stuff", input_schema={"type": "object"}, server="my_server")
        assert t.name == "my_tool"
        assert t.description == "Does stuff"
        assert t.input_schema == {"type": "object"}
        assert t.server == "my_server"

    def test_missing_field(self):
        with pytest.raises(TypeError):
            ToolSchema(name="x")  # type: ignore[call-arg]

    def test_frozen(self):
        t = ToolSchema(name="x", description="y", input_schema={}, server="s")
        with pytest.raises(AttributeError):
            t.name = "z"  # type: ignore[misc]


class TestSessionConfig:
    def test_defaults(self):
        c = SessionConfig(model="sonnet", profile="work")
        assert c.model == "sonnet"
        assert c.profile == "work"
        assert c.cwd is None
        assert c.binary is None
        assert c.sandbox is None
        assert c.system_prompt is None
        assert c.tools is None
        assert c.extra_args is None
        assert c.env is None
        assert c.resume_session_id is None
        assert c.session_resolution is None
        assert c.debug is None
        assert c.mcp is None
        assert c.plugins is None
        assert c.stream is None
        assert c.process_limits is None
        assert c.budget is None
        assert c.effort is None
        assert c.json_schema is None
        assert c.fallback_model is None
        assert c.betas is None
        assert c.add_dirs is None
        assert c.builtin_tools is None
        assert c.brief is False
        assert c.settings is None
        assert c.setting_sources is None
        assert c.file_specs is None
        assert c.agent_name is None
        assert c.agents_json is None
        assert c.hooks is None
        assert c.no_persistence is False

    def test_poll_timeout_default(self):
        c = SessionConfig(model="sonnet", profile="work")
        assert c.poll_timeout == 1.0

    def test_join_timeout_default(self):
        c = SessionConfig(model="sonnet", profile="work")
        assert c.join_timeout == 5.0

    def test_custom_poll_timeout(self):
        c = SessionConfig(model="sonnet", profile="work", poll_timeout=0.5)
        assert c.poll_timeout == 0.5

    def test_custom_join_timeout(self):
        c = SessionConfig(model="sonnet", profile="work", join_timeout=10.0)
        assert c.join_timeout == 10.0

    def test_missing_required(self):
        with pytest.raises(TypeError):
            SessionConfig()  # type: ignore[call-arg]

    def test_missing_profile(self):
        with pytest.raises(TypeError):
            SessionConfig(model="sonnet")  # type: ignore[call-arg]

    def test_effort(self):
        c = SessionConfig(model="sonnet", profile="work", effort="high")
        assert c.effort == "high"

    def test_debug_option(self):
        c = SessionConfig(
            model="sonnet",
            profile="work",
            debug=DebugOptions(enabled=True, filter=None, file=None),
        )
        assert c.debug is not None
        assert c.debug.enabled is True
        assert c.debug.filter is None

    def test_budget_option(self):
        c = SessionConfig(
            model="sonnet",
            profile="work",
            budget=Budget(max_turns=5),
        )
        assert c.budget is not None
        assert c.budget.max_turns == 5

    def test_process_limits_flow_to_process_config(self):
        """ProcessLimits values flow through _build_process_config to ProcessConfig."""
        from unittest.mock import patch, AsyncMock
        from claudestream._async_session import AsyncSession

        limits = ProcessLimits(
            buffer_limit=4_000_000,
            shutdown_timeout=3.0,
            version_check_timeout=1.0,
            health_timeout=15.0,
        )
        config = SessionConfig(
            model="sonnet",
            profile="test",
            process_limits=limits,
        )

        with patch("claudestream._async_session.find_binary", return_value="/fake/claude"), \
             patch("claudestream._async_session.check_version", new_callable=AsyncMock, return_value="2.1.0"), \
             patch("claudewheel.profile.resolve_profile", return_value={}):
            session = AsyncSession(config)

        assert session._process_mgr.config.buffer_limit == 4_000_000
        assert session._process_mgr.config.shutdown_timeout == 3.0
        assert session._health_timeout == 15.0

    def test_frozen(self):
        c = SessionConfig(model="sonnet", profile="work")
        with pytest.raises(AttributeError):
            c.model = "opus"  # type: ignore[misc]
