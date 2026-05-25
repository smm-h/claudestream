"""AgentDefinition, Budget, .agent.json loader, and invoke_agent context managers."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

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


def _build_sandbox(definition: AgentDefinition):
    """Build a Sandbox from an AgentDefinition's SandboxConfig, or None."""
    if definition.sandbox is None:
        return None
    from claudestream.policy import Sandbox
    return Sandbox(
        tools=definition.sandbox.tools,
        bare=definition.sandbox.bare,
        write_paths=definition.sandbox.write_paths,
    )


def _build_tools(
    definition: AgentDefinition,
    tool_handlers: dict[str, Any] | None,
) -> list | None:
    """Build Tool objects from ToolSchemas + handlers, or None."""
    if not definition.tools or not tool_handlers:
        return None
    from claudestream._tools import Tool
    tools = []
    for ts in definition.tools:
        handler = tool_handlers.get(ts.name)
        if handler:
            tools.append(Tool(
                name=ts.name,
                description=ts.description,
                input_schema=ts.input_schema,
                handler=handler,
                server=ts.server,
            ))
    return tools or None


def _resolve_model(model: str | None, definition: AgentDefinition) -> str:
    """Return the effective model, raising if neither source provides one."""
    effective = model or definition.model
    if not effective:
        raise ValueError(
            "model must be specified either in the agent definition or as an argument"
        )
    return effective


@asynccontextmanager
async def invoke_agent(
    definition: AgentDefinition,
    profile: str,
    *,
    variables: dict[str, str] | None = None,
    model: str | None = None,
    tool_handlers: dict[str, Any] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
):
    """Create and manage an AsyncSession from an AgentDefinition.

    Resolves the prompt template, builds sandbox and tools from the definition,
    and yields a ready-to-use AsyncSession.

    Raises:
        ValueError: If model is not specified in the definition or as an argument.
        ValueError: If prompt template has unresolved variables.
    """
    from claudestream._async_session import AsyncSession

    prompt = resolve_prompt(definition.prompt_template, variables or {})
    effective_model = _resolve_model(model, definition)
    sandbox = _build_sandbox(definition)
    tools = _build_tools(definition, tool_handlers)

    async with AsyncSession(
        effective_model,
        profile,
        sandbox=sandbox,
        tools=tools,
        system_prompt=prompt,
        cwd=cwd,
        env=env,
    ) as session:
        yield session


@contextmanager
def invoke_agent_sync(
    definition: AgentDefinition,
    profile: str,
    *,
    variables: dict[str, str] | None = None,
    model: str | None = None,
    tool_handlers: dict[str, Any] | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
):
    """Create and manage a SyncSession from an AgentDefinition.

    Sync version of invoke_agent.

    Raises:
        ValueError: If model is not specified in the definition or as an argument.
        ValueError: If prompt template has unresolved variables.
    """
    from claudestream._sync_session import SyncSession

    prompt = resolve_prompt(definition.prompt_template, variables or {})
    effective_model = _resolve_model(model, definition)
    sandbox = _build_sandbox(definition)
    tools = _build_tools(definition, tool_handlers)

    with SyncSession(
        effective_model,
        profile,
        sandbox=sandbox,
        tools=tools,
        system_prompt=prompt,
        cwd=cwd,
        env=env,
    ) as session:
        yield session
