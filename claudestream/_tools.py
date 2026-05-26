"""Tool registration API providing the Tool struct and a decorator for defining user tools that are served via MCP to Claude Code."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin

import msgspec

__all__ = [
    "Tool",
    "tool",
]


class Tool(msgspec.Struct, frozen=True):
    """A user-defined tool that can be served via MCP to Claude Code."""

    name: str
    description: str
    input_schema: dict
    handler: Any
    server: str


# Type-hint to JSON Schema type mapping
_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}

# Item-type mapping for list[T] generics
_ITEM_TYPE_MAP: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _type_to_schema(annotation: Any) -> dict:
    """Convert a single type annotation to a JSON Schema fragment."""
    # Direct type match
    if annotation in _TYPE_MAP:
        schema: dict[str, Any] = {"type": _TYPE_MAP[annotation]}
        return schema

    # Generic types like list[str], list[int], etc.
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        if args and args[0] is not Any and args[0] in _ITEM_TYPE_MAP:
            return {"type": "array", "items": {"type": _ITEM_TYPE_MAP[args[0]]}}
        return {"type": "array"}

    # Fallback: treat as object
    return {"type": "object"}


def _generate_schema(fn: Callable) -> dict:
    """Generate a JSON Schema from a function's type hints and defaults."""
    sig = inspect.signature(fn)
    hints = _get_type_hints_safe(fn)

    properties: dict[str, dict] = {}
    required: list[str] = []

    for param_name, param in sig.parameters.items():
        # Skip self/cls and return annotation
        if param_name in ("self", "cls"):
            continue

        annotation = hints.get(param_name, inspect.Parameter.empty)

        if annotation is inspect.Parameter.empty:
            # No type hint -- skip from properties (still a valid param)
            continue

        properties[param_name] = _type_to_schema(annotation)

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _get_type_hints_safe(fn: Callable) -> dict[str, Any]:
    """Get type hints, falling back to __annotations__ on failure."""
    try:
        # get_type_hints resolves string annotations
        from typing import get_type_hints
        return get_type_hints(fn)
    except Exception:
        return getattr(fn, "__annotations__", {})


def tool(
    server: str,
    *,
    name: str | None = None,
    description: str | None = None,
) -> Callable:
    """Decorator factory that creates a Tool from a function's type hints and docstring.

    Usage::

        @tool("my_server")
        async def create_child(name: str, age: int) -> str:
            \"\"\"Create a child record.\"\"\"
            ...

        @tool("my_server", name="custom_name")
        def greet(message: str) -> str:
            ...
    """

    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        tool_desc = description or (fn.__doc__ or "").strip().split("\n")[0] or tool_name
        schema = _generate_schema(fn)
        t = Tool(
            name=tool_name,
            description=tool_desc,
            input_schema=schema,
            handler=fn,
            server=server,
        )
        fn._tool = t  # type: ignore[attr-defined]
        return fn

    return decorator
