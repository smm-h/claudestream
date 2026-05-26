"""Tests for option structs: construction, missing fields, and immutability."""

import pytest

from claudestream._options import (
    SessionResolution,
    DebugOptions,
    McpOptions,
    PluginOptions,
    StreamOptions,
    ProcessLimits,
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
