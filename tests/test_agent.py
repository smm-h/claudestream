"""Tests for AgentDefinition, Budget, ToolSchema, SandboxConfig, .agent.json loader, and invoke_agent."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from claudestream._agent import (
    AgentDefinition,
    Budget,
    SandboxConfig,
    ToolSchema,
    _build_sandbox,
    _build_tools,
    _resolve_model,
    invoke_agent,
    invoke_agent_sync,
    load_agent,
    resolve_prompt,
)
from claudestream._options import SessionConfig
from claudestream.policy import Sandbox
from claudestream._tools import Tool


class TestBudget:
    def test_budget_defaults(self):
        b = Budget()
        assert b.max_cost_usd is None
        assert b.max_turns is None
        assert b.max_tokens is None

    def test_budget_with_values(self):
        b = Budget(max_cost_usd=5.0, max_turns=10, max_tokens=100_000)
        assert b.max_cost_usd == 5.0
        assert b.max_turns == 10
        assert b.max_tokens == 100_000


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


class TestSandboxConfig:
    def test_sandbox_config_construction(self):
        sc = SandboxConfig(
            tools=["Read", "Write"],
            bare=True,
            write_paths=["/tmp"],
        )
        assert sc.tools == ["Read", "Write"]
        assert sc.bare is True
        assert sc.write_paths == ["/tmp"]

    def test_sandbox_config_defaults(self):
        sc = SandboxConfig()
        assert sc.tools is None
        assert sc.bare is False
        assert sc.write_paths is None


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
        sc = SandboxConfig(tools=["Read"], bare=True)
        b = Budget(max_cost_usd=1.0, max_turns=5, max_tokens=50_000)
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
            "budget": {"max_cost_usd": 2.5, "max_turns": 20, "max_tokens": 200000},
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
        assert ad.budget.max_cost_usd == 2.5
        assert ad.budget.max_turns == 20
        assert ad.budget.max_tokens == 200000
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
        sc = SandboxConfig(tools=["Read"])
        b = Budget(max_cost_usd=1.0)
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


class TestResolveModel:
    def test_arg_overrides_definition(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", model="haiku")
        assert _resolve_model("opus", ad) == "opus"

    def test_definition_model_used(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", model="haiku")
        assert _resolve_model(None, ad) == "haiku"

    def test_no_model_raises(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        with pytest.raises(ValueError, match="model must be specified"):
            _resolve_model(None, ad)

    def test_empty_string_model_raises(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        with pytest.raises(ValueError, match="model must be specified"):
            _resolve_model("", ad)


class TestBuildSandbox:
    def test_no_sandbox_config(self):
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0")
        assert _build_sandbox(ad) is None

    def test_sandbox_from_config(self):
        sc = SandboxConfig(tools=["Read", "Write"], bare=True, write_paths=["/tmp"])
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", sandbox=sc)
        sandbox = _build_sandbox(ad)
        assert isinstance(sandbox, Sandbox)
        assert sandbox.tools == ["Read", "Write"]
        assert sandbox.bare is True
        assert sandbox.write_paths == ["/tmp"]

    def test_sandbox_defaults(self):
        sc = SandboxConfig()
        ad = AgentDefinition(name="a", prompt_template="p", version="1.0", sandbox=sc)
        sandbox = _build_sandbox(ad)
        assert isinstance(sandbox, Sandbox)
        assert sandbox.tools is None
        assert sandbox.bare is False
        assert sandbox.write_paths is None


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
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("claudestream._async_session.AsyncSession", return_value=mock_session) as mock_cls:
                async with invoke_agent(ad, "test-profile", variables={"name": "Alice"}) as session:
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

    def test_builds_sandbox(self):
        sc = SandboxConfig(tools=["Read"], bare=True)
        ad = AgentDefinition(
            name="test",
            prompt_template="p",
            version="1.0",
            model="sonnet",
            sandbox=sc,
        )
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("claudestream._async_session.AsyncSession", return_value=mock_session) as mock_cls:
                async with invoke_agent(ad, "profile") as session:
                    pass
                config = mock_cls.call_args.args[0]
                assert isinstance(config, SessionConfig)
                assert isinstance(config.sandbox, Sandbox)
                assert config.sandbox.tools == ["Read"]
                assert config.sandbox.bare is True

        asyncio.run(run())

    def test_requires_model(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0")

        async def run():
            with pytest.raises(ValueError, match="model must be specified"):
                async with invoke_agent(ad, "profile"):
                    pass

        asyncio.run(run())

    def test_model_override(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="haiku")
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        async def run():
            with patch("claudestream._async_session.AsyncSession", return_value=mock_session) as mock_cls:
                async with invoke_agent(ad, "profile", model="opus") as session:
                    pass
                config = mock_cls.call_args.args[0]
                assert isinstance(config, SessionConfig)
                assert config.model == "opus"

        asyncio.run(run())


class TestInvokeAgentSync:
    def test_resolves_prompt_and_creates_session(self):
        ad = AgentDefinition(
            name="test",
            prompt_template="Hello {name}!",
            version="1.0",
            model="sonnet",
        )
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, "test-profile", variables={"name": "Bob"}) as session:
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
        with pytest.raises(ValueError, match="model must be specified"):
            with invoke_agent_sync(ad, "profile"):
                pass

    def test_model_override(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="haiku")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(ad, "profile", model="opus") as session:
                pass
            config = mock_cls.call_args.args[0]
            assert isinstance(config, SessionConfig)
            assert config.model == "opus"

    def test_passes_cwd_and_env(self):
        ad = AgentDefinition(name="test", prompt_template="p", version="1.0", model="sonnet")
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)

        with patch("claudestream._sync_session.SyncSession", return_value=mock_session) as mock_cls:
            with invoke_agent_sync(
                ad, "profile", cwd="/work", env={"KEY": "val"}
            ) as session:
                pass
            config = mock_cls.call_args.args[0]
            assert isinstance(config, SessionConfig)
            assert config.cwd == "/work"
            assert config.env == {"KEY": "val"}
