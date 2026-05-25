"""AgentDefinition, Budget, and .agent.json loader."""

from __future__ import annotations

import re
from pathlib import Path

import msgspec


class Budget(msgspec.Struct, frozen=True):
    """Cost/turn/token limits for a session."""

    max_cost_usd: float | None = None
    max_turns: int | None = None
    max_tokens: int | None = None


class ToolSchema(msgspec.Struct, frozen=True):
    """Tool schema without handler -- for JSON-serializable agent definitions."""

    name: str
    description: str
    input_schema: dict
    server: str = "claudestream"


class SandboxConfig(msgspec.Struct, frozen=True):
    """Sandbox configuration for agent definitions (no runtime-only fields)."""

    tools: list[str] | None = None
    bare: bool = False
    write_paths: list[str] | None = None


class AgentDefinition(msgspec.Struct, frozen=True):
    """A complete agent definition, loadable from a .agent.json file."""

    name: str
    prompt_template: str
    version: str = "1.0"
    description: str = ""
    tools: list[ToolSchema] | None = None
    sandbox: SandboxConfig | None = None
    budget: Budget | None = None
    model: str | None = None


def resolve_prompt(template: str, variables: dict[str, str]) -> str:
    """Resolve {variable} placeholders in a prompt template.

    Raises:
        ValueError: If any placeholders remain after substitution.
    """
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", value)
    unresolved = re.findall(r"\{(\w+)\}", result)
    if unresolved:
        raise ValueError(f"Unresolved template variables: {', '.join(unresolved)}")
    return result


def load_agent(path: str | Path) -> AgentDefinition:
    """Load an AgentDefinition from a .agent.json file."""
    data = Path(path).read_bytes()
    return msgspec.json.decode(data, type=AgentDefinition)
