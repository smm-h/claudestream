"""Agent definition loader and budget enforcement for Claude Code sessions, with sync and async context managers for invoking agents."""

from __future__ import annotations

import logging
import os
import re
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

import msgspec

from claudestream._options import Budget, McpOptions, SessionConfig, SessionResolution, StreamOptions, ToolSchema
from claudestream.policy import Sandbox

log = logging.getLogger("claudestream")


class AgentDefinition(msgspec.Struct, frozen=True):
    """A complete agent definition, loadable from a .agent.json file."""

    name: str
    prompt_template: str
    version: str
    description: str = ""
    tools: list[ToolSchema] | None = None
    sandbox: Sandbox | None = None
    budget: Budget | None = None
    model: str | None = None
    mcp: McpOptions | None = None
    stream: StreamOptions | None = None


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
    """Load an AgentDefinition from a .agent.json file or by bare name.

    If ``path`` has no path separators and doesn't end with ``.json``, it is
    treated as a bare agent name.  The loader looks for
    ``.claudestream/agents/<name>.agent.json`` relative to the current working
    directory.
    """
    p = str(path)
    if os.sep not in p and (os.altsep is None or os.altsep not in p) and not p.endswith(".json"):
        # Bare name resolution
        expected = Path.cwd() / ".claudestream" / "agents" / f"{p}.agent.json"
        if not expected.exists():
            raise FileNotFoundError(
                f"Agent '{p}' not found at {expected}"
            )
        data = expected.read_bytes()
    else:
        data = Path(path).read_bytes()
    return msgspec.json.decode(data, type=AgentDefinition)


def discover_agents(cwd: str | None = None) -> list[AgentDefinition]:
    """Discover agent definitions in ``.claudestream/agents/``.

    Finds all ``*.agent.json`` files and returns a list of
    :class:`AgentDefinition` sorted by name.  Returns an empty list if the
    directory does not exist.
    """
    base = Path(cwd) if cwd else Path.cwd()
    agents_dir = base / ".claudestream" / "agents"
    if not agents_dir.is_dir():
        return []
    agents: list[AgentDefinition] = []
    for f in sorted(agents_dir.glob("*.agent.json")):
        agents.append(msgspec.json.decode(f.read_bytes(), type=AgentDefinition))
    agents.sort(key=lambda a: a.name)
    return agents


def _build_tools(
    definition: AgentDefinition,
    tool_handlers: dict[str, Any] | None,
) -> list | None:
    """Build Tool objects from ToolSchemas + handlers, or None."""
    if not definition.tools:
        return None
    if not tool_handlers:
        missing = [t.name for t in definition.tools]
        raise ValueError(f"Missing handlers for tools: {', '.join(missing)}")
    missing = [t.name for t in definition.tools if t.name not in tool_handlers]
    if missing:
        raise ValueError(f"Missing handlers for tools: {', '.join(missing)}")
    from claudestream._tools import Tool
    tools = []
    for ts in definition.tools:
        handler = tool_handlers[ts.name]
        tools.append(Tool(
            name=ts.name,
            description=ts.description,
            input_schema=ts.input_schema,
            handler=handler,
            server=ts.server,
        ))
    return tools or None


def _resolve_model(config: SessionConfig, definition: AgentDefinition) -> str:
    """Return the effective model: definition wins if set, then config.

    Raises:
        ValueError: If neither source provides a model.
    """
    effective = definition.model or config.model
    if not effective:
        raise ValueError(
            "model must be specified either in the agent definition or in the config"
        )
    return effective


def _build_session_resolution(definition: AgentDefinition) -> SessionResolution | None:
    """Build a SessionResolution from the agent name, or None if no name."""
    if not definition.name:
        return None
    return SessionResolution(
        name=definition.name,
        session_id=None,
        resume_session_id=None,
        continue_last=False,
        fork=False,
    )


@asynccontextmanager
async def invoke_agent(
    definition: AgentDefinition,
    config: SessionConfig,
    *,
    variables: dict[str, str] | None = None,
    tool_handlers: dict[str, Any] | None = None,
):
    """Create and manage an AsyncSession from an AgentDefinition.

    Uses ``config`` as the base configuration. Definition fields (model,
    sandbox, mcp, stream, system_prompt) override the config where set.

    Raises:
        ValueError: If model is not specified in the definition or config.
        ValueError: If prompt template has unresolved variables.
    """
    from claudestream._async_session import AsyncSession

    prompt = resolve_prompt(definition.prompt_template, variables or {})
    effective_model = _resolve_model(config, definition)
    tools = _build_tools(definition, tool_handlers)

    if definition.description:
        log.info("Agent: %s - %s", definition.name, definition.description)

    merged = SessionConfig(
        model=effective_model,
        profile=config.profile,
        sandbox=definition.sandbox if definition.sandbox is not None else config.sandbox,
        tools=tools,
        system_prompt=prompt,
        cwd=config.cwd,
        binary=config.binary,
        extra_args=config.extra_args,
        env=config.env,
        resume_session_id=config.resume_session_id,
        mcp=definition.mcp if definition.mcp is not None else config.mcp,
        stream=definition.stream if definition.stream is not None else config.stream,
        session_resolution=_build_session_resolution(definition),
        debug=config.debug,
        plugins=config.plugins,
        process_limits=config.process_limits,
        budget=definition.budget if definition.budget is not None else config.budget,
        poll_timeout=config.poll_timeout,
        join_timeout=config.join_timeout,
        effort=config.effort,
        json_schema=config.json_schema,
        fallback_model=config.fallback_model,
        betas=config.betas,
        add_dirs=config.add_dirs,
        builtin_tools=config.builtin_tools,
        brief=config.brief,
        settings=config.settings,
        setting_sources=config.setting_sources,
        file_specs=config.file_specs,
        agent_name=config.agent_name,
        agents_json=config.agents_json,
        hooks=config.hooks,
        no_persistence=config.no_persistence,
    )
    async with AsyncSession(merged) as session:
        yield session


@contextmanager
def invoke_agent_sync(
    definition: AgentDefinition,
    config: SessionConfig,
    *,
    variables: dict[str, str] | None = None,
    tool_handlers: dict[str, Any] | None = None,
):
    """Create and manage a SyncSession from an AgentDefinition.

    Sync version of invoke_agent. Uses ``config`` as the base configuration.
    Definition fields override the config where set.

    Raises:
        ValueError: If model is not specified in the definition or config.
        ValueError: If prompt template has unresolved variables.
    """
    from claudestream._sync_session import SyncSession

    prompt = resolve_prompt(definition.prompt_template, variables or {})
    effective_model = _resolve_model(config, definition)
    tools = _build_tools(definition, tool_handlers)

    if definition.description:
        log.info("Agent: %s - %s", definition.name, definition.description)

    merged = SessionConfig(
        model=effective_model,
        profile=config.profile,
        sandbox=definition.sandbox if definition.sandbox is not None else config.sandbox,
        tools=tools,
        system_prompt=prompt,
        cwd=config.cwd,
        binary=config.binary,
        extra_args=config.extra_args,
        env=config.env,
        resume_session_id=config.resume_session_id,
        mcp=definition.mcp if definition.mcp is not None else config.mcp,
        stream=definition.stream if definition.stream is not None else config.stream,
        session_resolution=_build_session_resolution(definition),
        debug=config.debug,
        plugins=config.plugins,
        process_limits=config.process_limits,
        budget=definition.budget if definition.budget is not None else config.budget,
        poll_timeout=config.poll_timeout,
        join_timeout=config.join_timeout,
        effort=config.effort,
        json_schema=config.json_schema,
        fallback_model=config.fallback_model,
        betas=config.betas,
        add_dirs=config.add_dirs,
        builtin_tools=config.builtin_tools,
        brief=config.brief,
        settings=config.settings,
        setting_sources=config.setting_sources,
        file_specs=config.file_specs,
        agent_name=config.agent_name,
        agents_json=config.agents_json,
        hooks=config.hooks,
        no_persistence=config.no_persistence,
    )
    with SyncSession(merged) as session:
        yield session
