"""Tests for Tool struct, @tool decorator, and schema generation."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch, MagicMock

from claudestream._async_session import AsyncSession
from claudestream._tools import Tool, tool, _generate_schema


class TestToolStruct:
    def test_construction_all_fields(self):
        def handler(x: str) -> str:
            return x

        t = Tool(
            name="my_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=handler,
            server="test_server",
        )
        assert t.name == "my_tool"
        assert t.description == "A test tool"
        assert t.input_schema == {"type": "object", "properties": {"x": {"type": "string"}}}
        assert t.handler is handler
        assert t.server == "test_server"

    def test_default_server(self):
        t = Tool(name="t", description="d", input_schema={}, handler=lambda: None)
        assert t.server == "claudestream"

    def test_frozen(self):
        t = Tool(name="t", description="d", input_schema={}, handler=lambda: None)
        try:
            t.name = "other"  # type: ignore[misc]
            assert False, "Should have raised"
        except AttributeError:
            pass


class TestToolDecorator:
    def test_async_function(self):
        @tool()
        async def create_child(name: str, age: int) -> str:
            """Create a child record."""
            return f"{name} {age}"

        t = create_child._tool
        assert isinstance(t, Tool)
        assert t.name == "create_child"
        assert t.description == "Create a child record."
        assert t.handler is create_child
        assert t.server == "claudestream"
        assert t.input_schema["properties"]["name"] == {"type": "string"}
        assert t.input_schema["properties"]["age"] == {"type": "integer"}
        assert "name" in t.input_schema["required"]
        assert "age" in t.input_schema["required"]

    def test_sync_function(self):
        @tool()
        def greet(message: str) -> str:
            """Say hello."""
            return message

        t = greet._tool
        assert isinstance(t, Tool)
        assert t.name == "greet"
        assert t.description == "Say hello."
        assert t.handler is greet

    def test_custom_server(self):
        @tool("my_server")
        def fn(x: str) -> str:
            """Do something."""
            return x

        assert fn._tool.server == "my_server"

    def test_custom_name(self):
        @tool(name="custom_name")
        def fn(x: str) -> str:
            """Do something."""
            return x

        assert fn._tool.name == "custom_name"

    def test_custom_description(self):
        @tool(description="custom desc")
        def fn(x: str) -> str:
            """Original docstring."""
            return x

        assert fn._tool.description == "custom desc"

    def test_no_docstring_uses_name(self):
        @tool()
        def my_func(x: str) -> str:
            ...

        assert my_func._tool.description == "my_func"

    def test_multiline_docstring_uses_first_line(self):
        @tool()
        def fn(x: str) -> str:
            """First line.

            More details here.
            """
            return x

        assert fn._tool.description == "First line."


class TestGenerateSchema:
    def test_str_param(self):
        def fn(x: str) -> str:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "string"}
        assert schema["required"] == ["x"]

    def test_int_param(self):
        def fn(x: int) -> int:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "integer"}

    def test_float_param(self):
        def fn(x: float) -> float:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "number"}

    def test_bool_param(self):
        def fn(x: bool) -> bool:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "boolean"}

    def test_list_str_param(self):
        def fn(x: list[str]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "array", "items": {"type": "string"}}

    def test_list_int_param(self):
        def fn(x: list[int]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "array", "items": {"type": "integer"}}

    def test_bare_list_param(self):
        def fn(x: list) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "array"}

    def test_dict_param(self):
        def fn(x: dict) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": "object"}

    def test_required_vs_optional(self):
        def fn(x: str, y: int = 42) -> None:
            ...

        schema = _generate_schema(fn)
        assert "x" in schema["required"]
        assert "y" not in schema["required"]
        # y should still be in properties
        assert "y" in schema["properties"]

    def test_no_type_hints(self):
        def fn(x, y):
            ...

        schema = _generate_schema(fn)
        assert schema["properties"] == {}
        assert "required" not in schema

    def test_skips_self_param(self):
        # Simulate a method-like function with self
        def fn(self, x: str) -> str:
            ...

        schema = _generate_schema(fn)
        assert "self" not in schema["properties"]
        assert "x" in schema["properties"]

    def test_return_annotation_excluded(self):
        def fn(x: str) -> str:
            ...

        schema = _generate_schema(fn)
        # return type should not appear in properties
        assert "return" not in schema["properties"]

    def test_multiple_params(self):
        def fn(name: str, count: int, active: bool, tags: list[str]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["name"] == {"type": "string"}
        assert schema["properties"]["count"] == {"type": "integer"}
        assert schema["properties"]["active"] == {"type": "boolean"}
        assert schema["properties"]["tags"] == {"type": "array", "items": {"type": "string"}}
        assert set(schema["required"]) == {"name", "count", "active", "tags"}


class TestSessionToolsParam:
    @patch("claudestream._async_session.find_binary", return_value="/usr/bin/claude")
    @patch("claudestream._async_session.check_version")
    def test_async_session_accepts_tools(self, mock_version, mock_binary):
        """AsyncSession constructor accepts tools parameter without error."""
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            @tool()
            def my_tool(x: str) -> str:
                """A tool."""
                return x

            session = AsyncSession(
                model="sonnet",
                profile="test",
                tools=[my_tool._tool],
            )
            assert len(session._user_tools) == 1
            assert session._user_tools[0].name == "my_tool"
            assert "claudestream" in session._tools_by_server
            assert len(session._tools_by_server["claudestream"]) == 1

    @patch("claudestream._async_session.find_binary", return_value="/usr/bin/claude")
    @patch("claudestream._async_session.check_version")
    def test_async_session_tools_grouped_by_server(self, mock_version, mock_binary):
        """Tools are grouped by server name."""
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            @tool("server_a")
            def tool_a(x: str) -> str:
                """Tool A."""
                return x

            @tool("server_b")
            def tool_b(y: int) -> int:
                """Tool B."""
                return y

            @tool("server_a")
            def tool_a2(z: bool) -> bool:
                """Tool A2."""
                return z

            session = AsyncSession(
                model="sonnet",
                profile="test",
                tools=[tool_a._tool, tool_b._tool, tool_a2._tool],
            )
            assert len(session._tools_by_server["server_a"]) == 2
            assert len(session._tools_by_server["server_b"]) == 1

    @patch("claudestream._async_session.find_binary", return_value="/usr/bin/claude")
    @patch("claudestream._async_session.check_version")
    def test_async_session_no_tools_default(self, mock_version, mock_binary):
        """AsyncSession with no tools parameter has empty tool lists."""
        with patch("claudewheel.profile.resolve_profile", return_value={}):
            session = AsyncSession(model="sonnet", profile="test")
            assert session._user_tools == []
            assert session._tools_by_server == {}
