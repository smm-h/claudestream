"""Agent definition loader and budget enforcement for Claude Code sessions, with sync and async context managers for invoking agents."""

from __future__ import annotations

import json as _json_mod
import logging
import os
import re
from contextlib import asynccontextmanager, contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

import msgspec

from claudestream._options import Budget, McpOptions, SessionConfig, SessionResolution, StreamOptions, ToolSchema
from claudestream.policy import Sandbox

log = logging.getLogger("claudestream")


class AgentDefinition(msgspec.Struct, frozen=True):
    """A complete agent definition, loadable from a .agent.json file."""

    name: str  # Agent identifier, also used as the session name
    prompt_template: str  # System prompt with {variable} placeholders to resolve
    version: str  # Schema version of this agent definition
    description: str = ""  # Human-readable summary of the agent's purpose
    tools: list[ToolSchema] | None = None  # Tool schemas the agent can use; None means no tools
    sandbox: Sandbox | None = None  # Tool/filesystem sandbox policy; overrides SessionConfig
    budget: Budget | None = None  # Cost/turn/token limits; overrides SessionConfig
    model: str | None = None  # Model override; None falls back to SessionConfig.model
    mcp: McpOptions | None = None  # External MCP server config; overrides SessionConfig
    stream: StreamOptions | None = None  # Stream output config; overrides SessionConfig


def resolve_prompt(template: str, variables: dict[str, str]) -> str:
    """Resolve {variable} placeholders in a prompt template.

    Only placeholders present in the *original* template are considered
    template variables.  Curly-brace patterns introduced by substituted
    values (e.g. ``{rects}`` inside a TypeScript API reference) are left
    as-is and do not trigger validation errors.

    Raises:
        ValueError: If any original template placeholders remain after
            substitution (i.e. the caller forgot to supply a variable).
    """
    # Identify which placeholders exist in the original template
    template_vars = set(re.findall(r"\{(\w+)\}", template))
    result = template
    for key, value in variables.items():
        result = result.replace("{" + key + "}", value)
    # Only flag placeholders that were in the original template and not resolved
    unresolved = template_vars - set(variables.keys())
    if unresolved:
        raise ValueError(f"Unresolved template variables: {', '.join(sorted(unresolved))}")
    return result


def load_agent(path: str | Path, cwd: str | None = None) -> AgentDefinition:
    """Load an AgentDefinition from a .agent.json file or by bare name.

    If ``path`` has no path separators and doesn't end with ``.json``, it is
    treated as a bare agent name.  The loader looks for
    ``.claudestream/agents/<name>.agent.json`` relative to *cwd* (or the
    current working directory when *cwd* is ``None``).
    """
    p = str(path)
    if os.sep not in p and (os.altsep is None or os.altsep not in p) and not p.endswith(".json"):
        # Bare name resolution
        base = Path(cwd) if cwd else Path.cwd()
        expected = base / ".claudestream" / "agents" / f"{p}.agent.json"
        if not expected.exists():
            raise FileNotFoundError(
                f"Agent '{p}' not found at {expected}"
            )
        data = expected.read_bytes()
    else:
        data = Path(path).read_bytes()
    agent_def = msgspec.json.decode(data, type=AgentDefinition)

    # Check for deprecated budget fields
    raw = _json_mod.loads(data)
    budget_dict = raw.get("budget")
    if isinstance(budget_dict, dict):
        deprecated = {"max_cost_usd", "max_turns", "max_tokens"}
        for field in sorted(deprecated & budget_dict.keys()):
            raise ValueError(
                f"Agent '{agent_def.name}' uses deprecated budget field '{field}'. "
                "Replace with threshold fields: cost_thresholds, turn_thresholds, "
                "token_thresholds. See migration guide."
            )

    return agent_def


def discover_agents(
    cwd: str | None = None,
    paths: list[str] | None = None,
    packages: list[str] | None = None,
) -> list[AgentDefinition]:
    """Discover agent definitions from multiple sources.

    Sources are searched in order; the first occurrence of each agent name wins.

    1. ``.claudestream/agents/`` relative to *cwd* (or the current working
       directory when *cwd* is ``None``).
    2. Each directory in *paths* (relative paths resolved against *cwd*).
    3. Each Python package in *packages* via ``importlib.resources``.

    Returns a deduplicated list of :class:`AgentDefinition` sorted by name.
    """
    base = Path(cwd) if cwd else Path.cwd()
    seen: dict[str, str] = {}  # name -> source description (for warnings)
    agents: list[AgentDefinition] = []

    def _add(agent: AgentDefinition, source: str) -> None:
        if agent.name in seen:
            log.warning(
                "Agent '%s' found in multiple locations, using first occurrence",
                agent.name,
            )
            return
        seen[agent.name] = source
        agents.append(agent)

    # 1. Default .claudestream/agents/ directory
    agents_dir = base / ".claudestream" / "agents"
    if agents_dir.is_dir():
        for f in sorted(agents_dir.glob("*.agent.json")):
            _add(msgspec.json.decode(f.read_bytes(), type=AgentDefinition), str(f))

    # 2. Custom paths
    if paths:
        for p in paths:
            d = Path(p)
            if not d.is_absolute():
                d = base / d
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.agent.json")):
                _add(msgspec.json.decode(f.read_bytes(), type=AgentDefinition), str(f))

    # 3. Package resources
    if packages:
        for package_name in packages:
            try:
                pkg_files = files(package_name)
            except (ModuleNotFoundError, TypeError, ValueError) as exc:
                log.warning("Could not load package '%s': %s", package_name, exc)
                continue
            try:
                items = list(pkg_files.iterdir())
            except (FileNotFoundError, OSError) as exc:
                log.warning("Could not iterate package '%s': %s", package_name, exc)
                continue
            for item in sorted(items, key=lambda x: x.name):
                if item.name.endswith(".agent.json"):
                    data = item.read_bytes()
                    _add(
                        msgspec.json.decode(data, type=AgentDefinition),
                        f"{package_name}/{item.name}",
                    )

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
        inject: list[str] = []
        if hasattr(handler, "_tool") and handler._tool.inject:
            inject = list(handler._tool.inject)
        tools.append(Tool(
            name=ts.name,
            description=ts.description,
            input_schema=ts.input_schema,
            handler=handler,
            server=ts.server,
            inject=inject,
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
        from_pr=config.from_pr,
        tool_context=config.tool_context,
        cost_log_path=config.cost_log_path,
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
        from_pr=config.from_pr,
        tool_context=config.tool_context,
        cost_log_path=config.cost_log_path,
    )
    with SyncSession(merged) as session:
        yield session
