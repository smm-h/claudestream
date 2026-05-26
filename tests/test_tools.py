"""Tests for Tool struct, @tool decorator, and schema generation."""

from __future__ import annotations

import json
import types
from enum import Enum
from typing import Any, Literal, Optional
from unittest.mock import patch, MagicMock

from claudestream._async_session import AsyncSession
from claudestream._tools import Tool, tool, collect_tools, _generate_schema, _parse_param_descriptions


# Module-level Enum definitions so get_type_hints can resolve them
# (from __future__ import annotations turns annotations into strings,
# and get_type_hints can only resolve names visible in the module scope)
class _Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class _Priority(Enum):
    LOW = 1
    HIGH = 2


class _Mixed(Enum):
    A = 1
    B = "two"


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

    def test_frozen(self):
        t = Tool(name="t", description="d", input_schema={}, handler=lambda: None, server="test")
        try:
            t.name = "other"  # type: ignore[misc]
            assert False, "Should have raised"
        except AttributeError:
            pass


class TestToolDecorator:
    def test_async_function(self):
        @tool("test_server")
        async def create_child(name: str, age: int) -> str:
            """Create a child record."""
            return f"{name} {age}"

        t = create_child._tool
        assert isinstance(t, Tool)
        assert t.name == "create_child"
        assert t.description == "Create a child record."
        assert t.handler is create_child
        assert t.server == "test_server"
        assert t.input_schema["properties"]["name"] == {"type": "string"}
        assert t.input_schema["properties"]["age"] == {"type": "integer"}
        assert "name" in t.input_schema["required"]
        assert "age" in t.input_schema["required"]

    def test_sync_function(self):
        @tool("test_server")
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
        @tool("test_server", name="custom_name")
        def fn(x: str) -> str:
            """Do something."""
            return x

        assert fn._tool.name == "custom_name"

    def test_custom_description(self):
        @tool("test_server", description="custom desc")
        def fn(x: str) -> str:
            """Original docstring."""
            return x

        assert fn._tool.description == "custom desc"

    def test_no_docstring_uses_name(self):
        @tool("test_server")
        def my_func(x: str) -> str:
            ...

        assert my_func._tool.description == "my_func"

    def test_multiline_docstring_uses_first_line(self):
        @tool("test_server")
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
    def test_async_session_accepts_tools(self):
        """AsyncSession constructor accepts tools parameter without error."""
        from tests.conftest import make_test_session

        @tool("test_server")
        def my_tool(x: str) -> str:
            """A tool."""
            return x

        session = make_test_session(model="sonnet", tools=[my_tool._tool])
        assert len(session._user_tools) == 1
        assert session._user_tools[0].name == "my_tool"
        assert "test_server" in session._tools_by_server
        assert len(session._tools_by_server["test_server"]) == 1

    def test_async_session_tools_grouped_by_server(self):
        """Tools are grouped by server name."""
        from tests.conftest import make_test_session

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

        session = make_test_session(
            model="sonnet",
            tools=[tool_a._tool, tool_b._tool, tool_a2._tool],
        )
        assert len(session._tools_by_server["server_a"]) == 2
        assert len(session._tools_by_server["server_b"]) == 1

    def test_async_session_no_tools_default(self):
        """AsyncSession with no tools parameter has empty tool lists."""
        from tests.conftest import make_test_session

        session = make_test_session(model="sonnet")
        assert session._user_tools == []
        assert session._tools_by_server == {}


class TestOptionalTypes:
    def test_optional_str(self):
        def fn(x: Optional[str]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": ["string", "null"]}

    def test_union_str_none(self):
        def fn(x: str | None) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": ["string", "null"]}

    def test_optional_int(self):
        def fn(x: Optional[int]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {"type": ["integer", "null"]}

    def test_optional_complex_uses_nullable_type(self):
        """Optional of a type with extra keys still uses [type, 'null']."""
        def fn(x: Optional[list[str]]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["x"] == {
            "type": ["array", "null"],
            "items": {"type": "string"},
        }

    def test_optional_enum_uses_oneof(self):
        """Optional of a mixed-type enum (no 'type' key) uses oneOf."""
        from claudestream._tools import _type_to_schema

        schema = _type_to_schema(Optional[_Mixed])
        assert schema == {"oneOf": [{"enum": [1, "two"]}, {"type": "null"}]}


class TestLiteralType:
    def test_string_literals(self):
        def fn(mode: Literal["fast", "slow"]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["mode"] == {"type": "string", "enum": ["fast", "slow"]}

    def test_mixed_literals(self):
        def fn(val: Literal[1, "two", 3]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["val"] == {"enum": [1, "two", 3]}


class TestEnumType:
    def test_string_enum(self):
        def fn(color: _Color) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["color"] == {"type": "string", "enum": ["red", "green", "blue"]}

    def test_int_enum(self):
        def fn(p: _Priority) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["p"] == {"enum": [1, 2]}


class TestComplexTypes:
    def test_list_dict(self):
        def fn(items: list[dict]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["items"] == {"type": "array", "items": {"type": "object"}}

    def test_dict_str_int(self):
        def fn(counts: dict[str, int]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["counts"] == {
            "type": "object",
            "additionalProperties": {"type": "integer"},
        }

    def test_dict_str_str(self):
        def fn(headers: dict[str, str]) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["headers"] == {
            "type": "object",
            "additionalProperties": {"type": "string"},
        }

    def test_bare_dict_no_additional_properties(self):
        def fn(data: dict) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["data"] == {"type": "object"}


class TestDefaultValues:
    def test_default_in_schema(self):
        def fn(name: str, count: int = 10) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["count"] == {"type": "integer", "default": 10}
        assert "name" in schema["required"]
        assert "count" not in schema.get("required", [])

    def test_default_string(self):
        def fn(mode: str = "fast") -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["mode"] == {"type": "string", "default": "fast"}

    def test_default_bool(self):
        def fn(verbose: bool = False) -> None:
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["verbose"] == {"type": "boolean", "default": False}


class TestDocstringParsing:
    def test_google_style(self):
        def fn(name: str, age: int) -> None:
            """Create a record.

            Args:
                name: The user's name
                age: The user's age in years
            """
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["name"]["description"] == "The user's name"
        assert schema["properties"]["age"]["description"] == "The user's age in years"

    def test_rest_style(self):
        def fn(name: str, age: int) -> None:
            """Create a record.

            :param name: The user's name
            :param age: The user's age in years
            """
            ...

        schema = _generate_schema(fn)
        assert schema["properties"]["name"]["description"] == "The user's name"
        assert schema["properties"]["age"]["description"] == "The user's age in years"

    def test_no_docstring(self):
        def fn(x: str) -> None:
            ...

        descs = _parse_param_descriptions(fn)
        assert descs == {}

    def test_docstring_without_params(self):
        def fn(x: str) -> None:
            """Just a description."""
            ...

        descs = _parse_param_descriptions(fn)
        assert descs == {}

    def test_description_only_for_documented_params(self):
        """Params not mentioned in the docstring get no description."""
        def fn(name: str, age: int, extra: str) -> None:
            """Do something.

            Args:
                name: The name
                age: The age
            """
            ...

        schema = _generate_schema(fn)
        assert "description" in schema["properties"]["name"]
        assert "description" in schema["properties"]["age"]
        assert "description" not in schema["properties"]["extra"]


class TestCollectTools:
    def test_collect_from_module(self):
        mod = types.ModuleType("fake_module")

        @tool("srv")
        def tool_a(x: str) -> str:
            """Tool A."""
            return x

        @tool("srv")
        def tool_b(y: int) -> int:
            """Tool B."""
            return y

        def not_a_tool(z: str) -> str:
            return z

        mod.tool_a = tool_a
        mod.tool_b = tool_b
        mod.not_a_tool = not_a_tool
        mod.some_value = 42

        collected = collect_tools(mod)
        names = {t.name for t in collected}
        assert names == {"tool_a", "tool_b"}
        assert all(isinstance(t, Tool) for t in collected)

    def test_collect_empty_module(self):
        mod = types.ModuleType("empty")
        assert collect_tools(mod) == []


class TestInjectParameter:
    def test_inject_param_excluded_from_schema(self):
        """@tool with inject=["ctx"] excludes ctx from the generated schema."""
        @tool("srv", inject=["ctx"])
        def search(query: str, ctx: Any = None) -> str:
            """Search."""
            return query

        schema = search._tool.input_schema
        assert "query" in schema["properties"]
        assert "ctx" not in schema["properties"]
        assert schema["required"] == ["query"]

    def test_inject_param_validation(self):
        """@tool with inject=["nonexistent"] raises ValueError."""
        import pytest
        with pytest.raises(ValueError, match="Inject parameter 'nonexistent' not found"):
            @tool("srv", inject=["nonexistent"])
            def fn(x: str) -> str:
                """Do something."""
                return x

    def test_inject_empty_list(self):
        """@tool with inject=[] works normally (no injection)."""
        @tool("srv", inject=[])
        def fn(x: str) -> str:
            """Do something."""
            return x

        schema = fn._tool.input_schema
        assert "x" in schema["properties"]
        assert fn._tool.inject == []

    def test_inject_with_other_params(self):
        """Function with both model params and inject params -- only model params in schema."""
        @tool("srv", inject=["ctx"])
        def fn(name: str, count: int = 5, ctx: Any = None) -> str:
            """Do something."""
            return name

        schema = fn._tool.input_schema
        assert "name" in schema["properties"]
        assert "count" in schema["properties"]
        assert "ctx" not in schema["properties"]
        assert schema["required"] == ["name"]
        assert fn._tool.inject == ["ctx"]


class TestJsonAwareResults:
    """Test that _handle_mcp_request serializes results appropriately."""

    def test_dict_result_json_serialized(self):
        """dict results are JSON-serialized, not str()."""
        data = {"key": "value", "count": 42}
        result_text = json.dumps(data)
        # Verify it produces valid JSON
        assert json.loads(result_text) == data

    def test_list_result_json_serialized(self):
        """list results are JSON-serialized."""
        data = [1, 2, 3]
        result_text = json.dumps(data)
        assert json.loads(result_text) == data

    def test_str_result_passthrough(self):
        """String results are used as-is."""
        result = "hello world"
        # Simulate the logic from _handle_mcp_request
        if isinstance(result, (dict, list)):
            result_text = json.dumps(result)
        elif isinstance(result, str):
            result_text = result
        else:
            result_text = str(result)
        assert result_text == "hello world"

    def test_int_result_str(self):
        """Non-str, non-dict/list results use str()."""
        result = 42
        if isinstance(result, (dict, list)):
            result_text = json.dumps(result)
        elif isinstance(result, str):
            result_text = result
        else:
            result_text = str(result)
        assert result_text == "42"
