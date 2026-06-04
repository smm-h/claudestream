"""Tests for AgentDefinition, Budget, ToolSchema, .agent.json loader, invoke_agent, discover_agents, and bare name resolution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import types
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from claudestream._agent import (
    AgentDefinition,
    Budget,
    ToolSchema,
    _build_tools,
    _build_session_resolution,
    _resolve_model,
    discover_agents,
    invoke_agent,
    invoke_agent_sync,
    load_agent,
    resolve_prompt,
)
from claudestream._options import SessionConfig, SessionResolution
from claudestream.policy import Sandbox
from claudestream._tools import Tool


class TestBudget:
    def test_budget_defaults(self):
        b = Budget()
        assert b.cost_thresholds == []
        assert b.turn_thresholds == []
        assert b.token_thresholds == []

    def test_budget_with_values(self):
        b = Budget(cost_thresholds=[5.0, 10.0], turn_thresholds=[10], token_thresholds=[100_000])
        assert b.cost_thresholds == [5.0, 10.0]
        assert b.turn_thresholds == [10]
        assert b.token_thresholds == [100_000]


class TestToolSchema:
    def test_tool_schema_construction(self):
        ts = ToolSchema(
            name="search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            server="my_server",
        )
        assert ts.name == "search"
        assert ts.description == "Search the web"
        assert ts.input_schema["properties"]["query"]["type"] == "string"
        assert ts.server == "my_server"

    def test_tool_schema_custom_server(self):
        ts = ToolSchema(
            name="t", description="d", input_schema={}, server="custom"
        )
        assert ts.server == "custom"


class TestAgentDefinition:
    def test_agent_definition_minimal(self):
        ad = AgentDefinition(name="assistant", prompt_template="You are helpful.", version="1.0")
        assert ad.name == "assistant"
        assert ad.prompt_template == "You are helpful."
        assert ad.version == "1.0"
        assert ad.description == ""
        assert ad.tools is None
        assert ad.sandbox is None
        assert ad.budget is None
        assert ad.model is None

    def test_agent_definition_full(self):
        ts = ToolSchema(name="t", description="d", input_schema={"type": "object"}, server="test")
        sc = Sandbox(tools=["Read"], bare=True)
        b = Budget(cost_thresholds=[1.0, 5.0], turn_thresholds=[5], token_thresholds=[50_000])
        ad = AgentDefinition(
            name="shop-assistant",
            prompt_template="Help the user buy {product}.",
            version="2.0",
            description="A shopping assistant",
            tools=[ts],
            sandbox=sc,
            budget=b,
            model="opus",
        )
        assert ad.name == "shop-assistant"
        assert ad.prompt_template == "Help the user buy {product}."
        assert ad.version == "2.0"
        assert ad.description == "A shopping assistant"
        assert ad.tools == [ts]
        assert ad.sandbox == sc
        assert ad.budget == b
        assert ad.model == "opus"

    def test_agent_definition_frozen(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        with pytest.raises(AttributeError):
            ad.name = "other"  # type: ignore[misc]


class TestResolvePrompt:
    def test_resolve_prompt_basic(self):
        result = resolve_prompt("Hello {name}!", {"name": "Alice"})
        assert result == "Hello Alice!"

    def test_resolve_prompt_multiple_vars(self):
        result = resolve_prompt(
            "{greeting} {name}, welcome to {place}.",
            {"greeting": "Hi", "name": "Bob", "place": "Paris"},
        )
        assert result == "Hi Bob, welcome to Paris."

    def test_resolve_prompt_unresolved(self):
        with pytest.raises(ValueError, match="Unresolved template variables: missing"):
            resolve_prompt("Hello {missing}!", {})

    def test_resolve_prompt_no_vars(self):
        result = resolve_prompt("No placeholders here.", {})
        assert result == "No placeholders here."

    def test_resolve_prompt_repeated_var(self):
        result = resolve_prompt("{x} and {x}", {"x": "hi"})
        assert result == "hi and hi"


class TestLoadAgent:
    def test_load_agent_minimal(self, tmp_path):
        data = {"name": "bot", "prompt_template": "Be helpful.", "version": "1.0"}
        path = tmp_path / "bot.agent.json"
        path.write_text(json.dumps(data))

        ad = load_agent(path)
        assert ad.name == "bot"
        assert ad.prompt_template == "Be helpful."
        assert ad.version == "1.0"
        assert ad.tools is None

    def test_load_agent_full(self, tmp_path):
        data = {
            "name": "shop",
            "prompt_template": "Help with {product}.",
            "version": "3.0",
            "description": "Shopping helper",
            "tools": [
                {
                    "name": "search",
                    "description": "Search products",
                    "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
                    "server": "product-api",
                }
            ],
            "sandbox": {"tools": ["Read", "Bash"], "bare": True, "write_paths": ["/out"]},
            "budget": {"cost_thresholds": [2.5, 5.0], "turn_thresholds": [20], "token_thresholds": [200000]},
            "model": "haiku",
        }
        path = tmp_path / "shop.agent.json"
        path.write_text(json.dumps(data))

        ad = load_agent(path)
        assert ad.name == "shop"
        assert ad.prompt_template == "Help with {product}."
        assert ad.version == "3.0"
        assert ad.description == "Shopping helper"
        assert len(ad.tools) == 1
        assert ad.tools[0].name == "search"
        assert ad.tools[0].server == "product-api"
        assert ad.sandbox.tools == ["Read", "Bash"]
        assert ad.sandbox.bare is True
        assert ad.sandbox.write_paths == ["/out"]
        assert ad.budget.cost_thresholds == [2.5, 5.0]
        assert ad.budget.turn_thresholds == [20]
        assert ad.budget.token_thresholds == [200000]
        assert ad.model == "haiku"

    def test_load_agent_invalid_json(self, tmp_path):
        path = tmp_path / "bad.agent.json"
        path.write_text("not json {{{")

        with pytest.raises(msgspec.DecodeError):
            load_agent(path)

    def test_load_agent_missing_required(self, tmp_path):
        data = {"prompt_template": "no name"}
        path = tmp_path / "no_name.agent.json"
        path.write_text(json.dumps(data))

        with pytest.raises(msgspec.DecodeError):
            load_agent(path)

    def test_roundtrip(self):
        ts = ToolSchema(name="t", description="d", input_schema={"type": "object"}, server="test")
        sc = Sandbox(tools=["Read"])
        b = Budget(cost_thresholds=[1.0])
        ad = AgentDefinition(
            name="roundtrip",
            prompt_template="test",
            version="1.0",
            tools=[ts],
            sandbox=sc,
            budget=b,
            model="sonnet",
        )
        encoded = msgspec.json.encode(ad)
        decoded = msgspec.json.decode(encoded, type=AgentDefinition)
        assert decoded == ad


class TestLoadAgentMigrationGuard:
    def test_max_cost_usd_raises(self, tmp_path):
        data = {
            "name": "old-agent",
            "prompt_template": "p",
            "version": "1.0",
            "budget": {"max_cost_usd": 5.0},
        }
        path = tmp_path / "old.agent.json"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="deprecated budget field 'max_cost_usd'"):
            load_agent(path)

    def test_max_turns_raises(self, tmp_path):
        data = {
            "name": "old-agent",
            "prompt_template": "p",
            "version": "1.0",
            "budget": {"max_turns": 10},
        }
        path = tmp_path / "old.agent.json"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="deprecated budget field 'max_turns'"):
            load_agent(path)

    def test_max_tokens_raises(self, tmp_path):
        data = {
            "name": "old-agent",
            "prompt_template": "p",
            "version": "1.0",
            "budget": {"max_tokens": 50000},
        }
        path = tmp_path / "old.agent.json"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match="deprecated budget field 'max_tokens'"):
            load_agent(path)

    def test_new_fields_pass(self, tmp_path):
        data = {
            "name": "new-agent",
            "prompt_template": "p",
            "version": "1.0",
            "budget": {"cost_thresholds": [5.0], "turn_thresholds": [10]},
        }
        path = tmp_path / "new.agent.json"
        path.write_text(json.dumps(data))

        ad = load_agent(path)
        assert ad.budget.cost_thresholds == [5.0]
        assert ad.budget.turn_thresholds == [10]


class TestResolveModel:
    def test_definition_overrides_config(self):
        cfg = SessionConfig(model="sonnet", profile="p")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", model="haiku")
        assert _resolve_model(cfg, ad) == "haiku"

    def test_config_model_used(self):
        cfg = SessionConfig(model="opus", profile="p")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        assert _resolve_model(cfg, ad) == "opus"

    def test_no_model_raises(self):
        cfg = SessionConfig(model="", profile="p")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        with pytest.raises(ValueError, match="model must be specified"):
            _resolve_model(cfg, ad)

    def test_empty_string_both_raises(self):
        cfg = SessionConfig(model="", profile="p")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", model="")
        with pytest.raises(ValueError, match="model must be specified"):
            _resolve_model(cfg, ad)


class TestBuildTools:
    def test_no_tools_in_definition(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        assert _build_tools(ad, {"search": lambda: None}) is None

    def test_no_handlers(self):
        ts = ToolSchema(name="search", description="s", input_schema={}, server="test")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", tools=[ts])
        with pytest.raises(ValueError, match="Missing handlers for tools: search"):
            _build_tools(ad, None)

    def test_builds_tools_from_handlers(self):
        ts = ToolSchema(
            name="search",
            description="Search things",
            input_schema={"type": "object"},
            server="my-server",
        )
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", tools=[ts])

        def handler(): pass
        tools = _build_tools(ad, {"search": handler})
        assert tools is not None
        assert len(tools) == 1
        assert isinstance(tools[0], Tool)
        assert tools[0].name == "search"
        assert tools[0].description == "Search things"
        assert tools[0].input_schema == {"type": "object"}
        assert tools[0].handler is handler
        assert tools[0].server == "my-server"

    def test_missing_handler_raises(self):
        ts1 = ToolSchema(name="a", description="d", input_schema={}, server="test")
        ts2 = ToolSchema(name="b", description="d", input_schema={}, server="test")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", tools=[ts1, ts2])

        def handler_a(): pass
        with pytest.raises(ValueError, match="Missing handlers for tools: b"):
            _build_tools(ad, {"a": handler_a})

    def test_all_handlers_missing_raises(self):
        ts = ToolSchema(name="a", description="d", input_schema={}, server="test")
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", tools=[ts])
        with pytest.raises(ValueError, match="Missing handlers for tools: a"):
            _build_tools(ad, {"nonexistent": lambda: None})


class TestInvokeAgent:
    def test_resolves_prompt_and_creates_session(self):
        ad = AgentDefinition(
            name="test",
            prompt_template="Hello {name}!",
            version="1.0",
            model="sonnet",
        )
        base_config = SessionConfig(model="ignored", profile="test-profile")
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("claudestream._async_session.AsyncSession", return_value=mock_session) as mock_cls:
                async with invoke_agent(ad, base_config, variables={"name": "Alice"}) as session:
                    assert session is mock_session
                mock_cls.assert_called_once()
                config = mock_cls.call_args.args[0]
                assert isinstance(config, SessionConfig)
                assert config.model == "sonnet"
                assert config.profile == "test-profile"
                assert config.sandbox is None
                assert config.tools is None
                assert config.system_prompt == "Hello Alice!"
                assert config.cwd is None
                assert config.env is None

        asyncio.run(run())

    def test_passes_sandbox(self):
        sc = Sandbox(tools=["Read"], bare=True)
        ad = AgentDefinition(
            name="test",
            prompt_template="p",
            version="1.0",
            model="sonnet",
            sandbox=sc,
        )
        base_config = SessionConfig(model="sonnet", profile="profile")
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("claudestream._async_session.AsyncSession", return_value=mock_session) as mock_cls:
                async with invoke_agent(ad, base_config) as session:
                    pass
                config = mock_cls.call_args.args[0]
                assert isinstance(config, SessionConfig)
                assert isinstance(config.sandbox, Sandbox)
                assert config.sandbox.tools == ["Read"]
                assert config.sandbox.bare is True

        asyncio.run(run())

    def test_requires_model(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0")
        base_config = SessionConfig(model="", profile="profile")

        async def run():
            with pytest.raises(ValueError, match="model must be specified"):
                async with invoke_agent(ad, base_config):
                    pass

        asyncio.run(run())

    def test_definition_model_overrides_config(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="haiku")
        base_config = SessionConfig(model="opus", profile="profile")
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("claudestream._async_session.AsyncSession", return_value=mock_session) as mock_cls:
                async with invoke_agent(ad, base_config) as session:
                    pass
                config = mock_cls.call_args.args[0]
                assert isinstance(config, SessionConfig)
                assert config.model == "haiku"

        asyncio.run(run())


class TestInvokeAgentSync:
    def test_resolves_prompt_and_creates_session(self):
        ad = AgentDefinition(
            name="test",
            prompt_template="Hello {name}!",
            version="1.0",
            model="sonnet",
        )
        base_config = SessionConfig(model="ignored", profile="test-profile")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, base_config, variables={"name": "Bob"}) as session:
                assert session is mock_session
            mock_cls.assert_called_once()
            config = mock_cls.call_args.args[0]
            assert isinstance(config, SessionConfig)
            assert config.model == "sonnet"
            assert config.profile == "test-profile"
            assert config.sandbox is None
            assert config.tools is None
            assert config.system_prompt == "Hello Bob!"
            assert config.cwd is None
            assert config.env is None

    def test_requires_model(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0")
        base_config = SessionConfig(model="", profile="profile")
        with pytest.raises(ValueError, match="model must be specified"):
            with invoke_agent_sync(ad, base_config):
                pass

    def test_definition_model_overrides_config(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="haiku")
        base_config = SessionConfig(model="opus", profile="profile")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, base_config) as session:
                pass
            config = mock_cls.call_args.args[0]
            assert isinstance(config, SessionConfig)
            assert config.model == "haiku"

    def test_passes_cwd_and_env_from_config(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="sonnet")
        base_config = SessionConfig(model="sonnet", profile="profile", cwd="/work", env={"KEY": "val"})
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, base_config) as session:
                pass
            config = mock_cls.call_args.args[0]
            assert isinstance(config, SessionConfig)
            assert config.cwd == "/work"
            assert config.env == {"KEY": "val"}

    def test_name_wired_to_session_resolution(self):
        ad = AgentDefinition(
            name="my-agent",
            prompt_template="p",
            version="1.0",
            model="sonnet",
        )
        base_config = SessionConfig(model="sonnet", profile="profile")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, base_config) as session:
                pass
            config = mock_cls.call_args.args[0]
            assert config.session_resolution is not None
            assert config.session_resolution.name == "my-agent"

    def test_description_logged(self, caplog):
        ad = AgentDefinition(
            name="my-agent",
            prompt_template="p",
            version="1.0",
            model="sonnet",
            description="A helpful agent",
        )
        base_config = SessionConfig(model="sonnet", profile="profile")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with caplog.at_level(logging.INFO, logger="claudestream"):
            with patch("claudestream._sync_session.SyncSession", return_value=mock_session):
                with invoke_agent_sync(ad, base_config) as session:
                    pass
        assert "Agent: my-agent - A helpful agent" in caplog.text

    def test_cost_log_path_passes_through(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="sonnet")
        base_config = SessionConfig(
            model="sonnet", profile="profile", cost_log_path="/tmp/costs.jsonl"
        )
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, base_config) as session:
                pass
            config = mock_cls.call_args.args[0]
            assert config.cost_log_path == "/tmp/costs.jsonl"

    def test_from_pr_passes_through(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="sonnet")
        base_config = SessionConfig(
            model="sonnet", profile="profile", from_pr="123"
        )
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, base_config) as session:
                pass
            config = mock_cls.call_args.args[0]
            assert config.from_pr == "123"

    def test_no_description_not_logged(self, caplog):
        ad = AgentDefinition(
            name="my-agent",
            prompt_template="p",
            version="1.0",
            model="sonnet",
        )
        base_config = SessionConfig(model="sonnet", profile="profile")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with caplog.at_level(logging.INFO, logger="claudestream"):
            with patch("claudestream._sync_session.SyncSession", return_value=mock_session):
                with invoke_agent_sync(ad, base_config) as session:
                    pass
        assert "Agent:" not in caplog.text


class TestBuildSessionResolution:
    def test_with_name(self):
        ad = AgentDefinition(name="test-agent", prompt_template="p", version="1.0")
        sr = _build_session_resolution(ad)
        assert sr is not None
        assert sr.name == "test-agent"
        assert sr.session_id is None
        assert sr.resume_session_id is None
        assert sr.continue_last is False
        assert sr.fork is False

    def test_empty_name_returns_none(self):
        ad = AgentDefinition(name="", prompt_template="p", version="1.0")
        assert _build_session_resolution(ad) is None


class TestLoadAgentBareName:
    def test_bare_name_resolution(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        data = {"name": "mybot", "prompt_template": "Hello.", "version": "1.0"}
        (agents_dir / "mybot.agent.json").write_text(json.dumps(data))

        monkeypatch.chdir(tmp_path)
        ad = load_agent("mybot")
        assert ad.name == "mybot"
        assert ad.version == "1.0"

    def test_bare_name_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError, match="Agent 'nonexistent' not found"):
            load_agent("nonexistent")

    def test_file_path_still_works(self, tmp_path):
        data = {"name": "bot", "prompt_template": "Hi.", "version": "2.0"}
        path = tmp_path / "bot.agent.json"
        path.write_text(json.dumps(data))

        ad = load_agent(str(path))
        assert ad.name == "bot"
        assert ad.version == "2.0"

    def test_json_extension_treated_as_path(self, tmp_path):
        data = {"name": "bot", "prompt_template": "Hi.", "version": "1.0"}
        path = tmp_path / "custom.json"
        path.write_text(json.dumps(data))

        ad = load_agent(str(path))
        assert ad.name == "bot"

    def test_path_with_separator_treated_as_path(self, tmp_path):
        subdir = tmp_path / "agents"
        subdir.mkdir()
        data = {"name": "bot", "prompt_template": "Hi.", "version": "1.0"}
        path = subdir / "bot.agent.json"
        path.write_text(json.dumps(data))

        ad = load_agent(str(path))
        assert ad.name == "bot"

    def test_bare_name_with_cwd(self, tmp_path):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        data = {"name": "mybot", "prompt_template": "Hello.", "version": "1.0"}
        (agents_dir / "mybot.agent.json").write_text(json.dumps(data))

        # Does not need monkeypatch.chdir -- cwd parameter is used instead
        ad = load_agent("mybot", cwd=str(tmp_path))
        assert ad.name == "mybot"
        assert ad.version == "1.0"

    def test_bare_name_with_cwd_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Agent 'ghost' not found"):
            load_agent("ghost", cwd=str(tmp_path))


class TestDiscoverAgents:
    def test_discover_agents(self, tmp_path):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        for name, ver in [("beta", "2.0"), ("alpha", "1.0"), ("gamma", "3.0")]:
            data = {"name": name, "prompt_template": "p", "version": ver}
            (agents_dir / f"{name}.agent.json").write_text(json.dumps(data))

        agents = discover_agents(str(tmp_path))
        assert len(agents) == 3
        assert [a.name for a in agents] == ["alpha", "beta", "gamma"]

    def test_discover_agents_empty_dir(self, tmp_path):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        agents = discover_agents(str(tmp_path))
        assert agents == []

    def test_discover_agents_no_dir(self, tmp_path):
        agents = discover_agents(str(tmp_path))
        assert agents == []

    def test_discover_agents_ignores_non_agent_json(self, tmp_path):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        # This should be picked up
        data = {"name": "bot", "prompt_template": "p", "version": "1.0"}
        (agents_dir / "bot.agent.json").write_text(json.dumps(data))
        # These should be ignored
        (agents_dir / "readme.md").write_text("docs")
        (agents_dir / "config.json").write_text("{}")

        agents = discover_agents(str(tmp_path))
        assert len(agents) == 1
        assert agents[0].name == "bot"

    def test_discover_agents_uses_cwd_when_none(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        data = {"name": "bot", "prompt_template": "p", "version": "1.0"}
        (agents_dir / "bot.agent.json").write_text(json.dumps(data))

        monkeypatch.chdir(tmp_path)
        agents = discover_agents(None)
        assert len(agents) == 1
        assert agents[0].name == "bot"

    def test_discover_with_custom_paths(self, tmp_path):
        custom_dir = tmp_path / "my_agents"
        custom_dir.mkdir()
        for name in ["alpha", "beta"]:
            data = {"name": name, "prompt_template": "p", "version": "1.0"}
            (custom_dir / f"{name}.agent.json").write_text(json.dumps(data))

        agents = discover_agents(str(tmp_path), paths=[str(custom_dir)])
        assert len(agents) == 2
        assert [a.name for a in agents] == ["alpha", "beta"]

    def test_discover_with_relative_custom_path(self, tmp_path):
        custom_dir = tmp_path / "extras"
        custom_dir.mkdir()
        data = {"name": "rel", "prompt_template": "p", "version": "1.0"}
        (custom_dir / "rel.agent.json").write_text(json.dumps(data))

        # Relative path resolved against cwd
        agents = discover_agents(str(tmp_path), paths=["extras"])
        assert len(agents) == 1
        assert agents[0].name == "rel"

    def test_discover_with_nonexistent_path(self, tmp_path):
        agents = discover_agents(str(tmp_path), paths=[str(tmp_path / "nope")])
        assert agents == []

    def test_discover_with_packages(self, tmp_path):
        # Create a fake package resource that yields .agent.json files
        agent_data = msgspec.json.encode(
            AgentDefinition(name="pkg-agent", prompt_template="p", version="1.0")
        )

        class FakeResource:
            name = "pkg-agent.agent.json"
            def read_bytes(self):
                return agent_data

        class FakeNonAgent:
            name = "readme.md"
            def read_bytes(self):
                return b""

        class FakePkgFiles:
            def iterdir(self):
                return [FakeNonAgent(), FakeResource()]

        with patch("claudestream._agent.files", return_value=FakePkgFiles()):
            agents = discover_agents(str(tmp_path), packages=["mypkg.agents"])
        assert len(agents) == 1
        assert agents[0].name == "pkg-agent"

    def test_discover_package_import_error(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="claudestream"):
            agents = discover_agents(str(tmp_path), packages=["nonexistent.pkg.xyz"])
        assert agents == []
        assert "Could not load package 'nonexistent.pkg.xyz'" in caplog.text

    def test_discover_deduplication(self, tmp_path):
        # Agent "dup" in default location
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        data_v1 = {"name": "dup", "prompt_template": "first", "version": "1.0"}
        (agents_dir / "dup.agent.json").write_text(json.dumps(data_v1))

        # Same agent name in a custom path
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        data_v2 = {"name": "dup", "prompt_template": "second", "version": "2.0"}
        (custom_dir / "dup.agent.json").write_text(json.dumps(data_v2))

        agents = discover_agents(str(tmp_path), paths=[str(custom_dir)])
        assert len(agents) == 1
        # First occurrence wins (from .claudestream/agents/)
        assert agents[0].prompt_template == "first"
        assert agents[0].version == "1.0"

    def test_discover_conflict_warning(self, tmp_path, caplog):
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        data = {"name": "dup", "prompt_template": "p", "version": "1.0"}
        (agents_dir / "dup.agent.json").write_text(json.dumps(data))

        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        (custom_dir / "dup.agent.json").write_text(json.dumps(data))

        with caplog.at_level(logging.WARNING, logger="claudestream"):
            discover_agents(str(tmp_path), paths=[str(custom_dir)])
        assert "Agent 'dup' found in multiple locations, using first occurrence" in caplog.text

    def test_discover_combined(self, tmp_path):
        # Default location
        agents_dir = tmp_path / ".claudestream" / "agents"
        agents_dir.mkdir(parents=True)
        d1 = {"name": "default-agent", "prompt_template": "p", "version": "1.0"}
        (agents_dir / "default-agent.agent.json").write_text(json.dumps(d1))

        # Custom path
        custom_dir = tmp_path / "custom"
        custom_dir.mkdir()
        d2 = {"name": "custom-agent", "prompt_template": "p", "version": "1.0"}
        (custom_dir / "custom-agent.agent.json").write_text(json.dumps(d2))

        # Package resource
        agent_data = msgspec.json.encode(
            AgentDefinition(name="pkg-agent", prompt_template="p", version="1.0")
        )

        class FakeResource:
            name = "pkg-agent.agent.json"
            def read_bytes(self):
                return agent_data

        class FakePkgFiles:
            def iterdir(self):
                return [FakeResource()]

        with patch("claudestream._agent.files", return_value=FakePkgFiles()):
            agents = discover_agents(
                str(tmp_path),
                paths=[str(custom_dir)],
                packages=["mypkg"],
            )
        assert len(agents) == 3
        assert [a.name for a in agents] == ["custom-agent", "default-agent", "pkg-agent"]
