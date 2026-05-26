"""Tool registration API providing the Tool struct and a decorator for defining user tools that are served via MCP to Claude Code."""

from __future__ import annotations

import inspect
import re
import types
from collections.abc import Callable
from enum import Enum
from types import ModuleType
from typing import Any, Literal, Union, get_args, get_origin

import msgspec

__all__ = [
    "Tool",
    "collect_tools",
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

    origin = get_origin(annotation)

    # Optional[T] / T | None  (Union with NoneType)
    if origin is Union or isinstance(annotation, types.UnionType):
        args = get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            base = _type_to_schema(non_none[0])
            if "type" in base and isinstance(base["type"], str):
                return {**base, "type": [base["type"], "null"]}
            return {"oneOf": [base, {"type": "null"}]}

    # Literal["a", "b"]
    if origin is Literal:
        values = list(get_args(annotation))
        if values and all(isinstance(v, str) for v in values):
            return {"type": "string", "enum": values}
        return {"enum": values}

    # Enum subclasses
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        values = [e.value for e in annotation]
        if all(isinstance(v, str) for v in values):
            return {"type": "string", "enum": values}
        return {"enum": values}

    # Generic types like list[str], list[int], list[dict], etc.
    if origin is list:
        args = get_args(annotation)
        if args and args[0] is not Any:
            if args[0] in _ITEM_TYPE_MAP:
                return {"type": "array", "items": {"type": _ITEM_TYPE_MAP[args[0]]}}
            if args[0] is dict:
                return {"type": "array", "items": {"type": "object"}}
            # Recursively resolve the item type
            return {"type": "array", "items": _type_to_schema(args[0])}
        return {"type": "array"}

    # dict[str, T]
    if origin is dict:
        args = get_args(annotation)
        if args and len(args) == 2:
            return {"type": "object", "additionalProperties": _type_to_schema(args[1])}
        return {"type": "object"}

    # Fallback: treat as object
    return {"type": "object"}


def _parse_param_descriptions(fn: Callable) -> dict[str, str]:
    """Extract per-parameter descriptions from a function's docstring.

    Supports Google-style (``Args:`` section) and reST-style (``:param name:``).
    """
    doc = inspect.getdoc(fn)
    if not doc:
        return {}

    descriptions: dict[str, str] = {}

    # reST-style:  :param name: description
    for m in re.finditer(r":param\s+(\w+)\s*:\s*(.+)", doc):
        descriptions[m.group(1)] = m.group(2).strip()

    if descriptions:
        return descriptions

    # Google-style: Args:\n    name: description
    args_match = re.search(r"Args:\s*\n((?:[ \t]+\w+:.+\n?)+)", doc)
    if args_match:
        for m in re.finditer(r"(\w+)\s*:\s*(.+)", args_match.group(1)):
            descriptions[m.group(1)] = m.group(2).strip()

    return descriptions


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
        else:
            properties[param_name]["default"] = param.default

    # Merge docstring descriptions
    descriptions = _parse_param_descriptions(fn)
    for param_name, desc in descriptions.items():
        if param_name in properties:
            properties[param_name]["description"] = desc

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


def collect_tools(module: ModuleType) -> list[Tool]:
    """Gather all @tool-decorated functions from a module."""
    tools: list[Tool] = []
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and hasattr(obj, "_tool"):
            tools.append(obj._tool)
    return tools
