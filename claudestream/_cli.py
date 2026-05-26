"""Command-line interface entry point for claudestream, providing send, listen, and agent commands for interacting with Claude Code."""

from __future__ import annotations

import json
import sys
from typing import Any

import msgspec
import strictcli

from claudestream import (
    AssistantText,
    ClaudeStreamError,
    CompactBoundary,
    Event,
    McpRequest,
    PermissionRequest,
    RateLimit,
    Result,
    Sandbox,
    SessionConfig,
    StreamDelta,
    SyncSession,
    Thinking,
    ToolResult,
    ToolUse,
    ApiRetry,
    SystemInit,
    UnknownEvent,
)
from claudestream._agent import discover_agents, invoke_agent_sync, load_agent
from claudestream._color import Colorizer, should_color

from importlib.metadata import version as _pkg_version

app = strictcli.App(
    name="claudestream",
    version=_pkg_version("claudestream"),
    help="Stream Claude Code's JSON protocol",
)


# --- Shared helpers ---


def _resolve_prompt(prompt: str, stdin: bool, color: Colorizer) -> str | int:
    """Resolve prompt from argument or stdin. Returns the prompt string, or 1 on error."""
    if stdin:
        if prompt:
            print(color.red("error: cannot use both prompt argument and --stdin"), file=sys.stderr)
            return 1
        prompt = sys.stdin.read().strip()
        if not prompt:
            print(color.red("error: --stdin provided but stdin is empty"), file=sys.stderr)
            return 1
    elif not prompt:
        print(color.red("error: prompt argument required (or use --stdin)"), file=sys.stderr)
        return 1
    return prompt


def _build_config(
    model: str,
    profile: str,
    cwd: str = "",
    skip_permissions: bool = False,
    system_prompt: str = "",
    resume: str = "",
) -> SessionConfig:
    """Build a SessionConfig from common CLI flags."""
    sandbox = Sandbox(skip_permissions=True) if skip_permissions else None
    return SessionConfig(
        model=model,
        cwd=cwd or None,
        sandbox=sandbox,
        profile=profile,
        system_prompt=system_prompt or None,
        resume_session_id=resume or None,
    )



def _run_with_session(
    config: SessionConfig,
    handler: Any,
    color: Colorizer,
) -> int | None:
    """Run handler(session) inside a SyncSession context with standard error handling."""
    try:
        with SyncSession(config) as session:
            handler(session)
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1
    return None


def _stream_events(session: SyncSession, prompt: str, footer: bool, color: Colorizer) -> None:
    """Shared streaming event loop used by cmd_stream and cmd_agent_run."""
    streamed_text = ""
    for event in session.send(prompt):
        if isinstance(event, StreamDelta) and event.text:
            streamed_text += event.text
            sys.stdout.write(event.text)
            sys.stdout.flush()
        elif isinstance(event, AssistantText):
            if event.text != streamed_text:
                sys.stdout.write(event.text)
                sys.stdout.flush()
        elif isinstance(event, Result):
            sys.stdout.write("\n")
            sys.stdout.flush()
            if footer:
                print(color.cyan(f"--- Done ({event.duration_ms:.0f}ms, ${event.total_cost_usd:.4f}) ---"), file=sys.stderr)
            streamed_text = ""
        elif isinstance(event, Thinking):
            print(color.dim("[thinking...]"), file=sys.stderr)
        elif isinstance(event, ToolUse):
            print(f"[tool: {color.bold(event.name)}]", file=sys.stderr)
        elif isinstance(event, ToolResult):
            print("[result]", file=sys.stderr)
        elif isinstance(event, ApiRetry):
            print(color.yellow(f"[retry {event.attempt}/{event.max_retries}: {event.error}]"), file=sys.stderr)
        elif isinstance(event, RateLimit):
            print(color.yellow(f"[rate limit: {event.status}]"), file=sys.stderr)
        elif isinstance(event, PermissionRequest):
            print(color.yellow(f"[permission: {event.tool_name}]"), file=sys.stderr)
        elif isinstance(event, (SystemInit, CompactBoundary, McpRequest, UnknownEvent)):
            pass


# --- send command ---

@app.command("send", help="Send a prompt and display the response")
@strictcli.arg("prompt", help="The prompt to send", required=False, default="")
@strictcli.flag("model", type=str, help="Model to use (e.g. sonnet, opus)", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("raw", type=bool, default=False, help="Show raw protocol events instead of flattened")
@strictcli.flag("json-output", type=bool, default=False, help="Output events as JSON lines")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("system-prompt", type=str, default="", help="System prompt for Claude", short="s")
@strictcli.flag("stdin", type=bool, default=False, help="Read prompt from stdin")
@strictcli.flag("no-color", type=bool, default=False, help="Disable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
def cmd_send(
    prompt: str = "",
    model: str = "",
    profile: str = "",
    cwd: str = "",
    raw: bool = False,
    json_output: bool = False,
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    stdin: bool = False,
    no_color: bool = False,
    resume: str = "",
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume)
    printer = EventPrinter(footer=footer, color=color)

    def handler(session: SyncSession) -> None:
        for event in session.send(prompt, raw=raw):
            if json_output:
                _print_json(event)
            else:
                printer.print_event(event)

    return _run_with_session(config, handler, color)


# --- stream command ---

@app.command("stream", help="Stream a prompt with real-time token output")
@strictcli.arg("prompt", help="The prompt to send", required=False, default="")
@strictcli.flag("model", type=str, help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("system-prompt", type=str, default="", help="System prompt for Claude", short="s")
@strictcli.flag("stdin", type=bool, default=False, help="Read prompt from stdin")
@strictcli.flag("no-color", type=bool, default=False, help="Disable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
def cmd_stream(
    prompt: str = "",
    model: str = "",
    profile: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    stdin: bool = False,
    no_color: bool = False,
    resume: str = "",
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume)

    def handler(session: SyncSession) -> None:
        _stream_events(session, prompt, footer, color)

    return _run_with_session(config, handler, color)


# --- events command ---

@app.command("events", help="Debug: show all raw protocol events")
@strictcli.arg("prompt", help="The prompt to send", required=False, default="")
@strictcli.flag("model", type=str, help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("system-prompt", type=str, default="", help="System prompt for Claude", short="s")
@strictcli.flag("stdin", type=bool, default=False, help="Read prompt from stdin")
@strictcli.flag("no-color", type=bool, default=False, help="Disable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
def cmd_events(
    prompt: str = "",
    model: str = "",
    profile: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    stdin: bool = False,
    no_color: bool = False,
    resume: str = "",
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume)

    def handler(session: SyncSession) -> None:
        for event in session.send(prompt, raw=True):
            _print_json(event)
            if footer and isinstance(event, Result):
                print(color.cyan(f"--- Done ({event.duration_ms:.0f}ms, ${event.total_cost_usd:.4f}) ---"), file=sys.stderr)

    return _run_with_session(config, handler, color)


# --- repl command ---

@app.command("repl", help="Interactive multi-turn REPL")
@strictcli.flag("model", type=str, help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("system-prompt", type=str, default="", help="System prompt for Claude", short="s")
@strictcli.flag("no-color", type=bool, default=False, help="Disable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
def cmd_repl(
    model: str,
    profile: str,
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    no_color: bool = False,
    resume: str = "",
) -> None:
    color = Colorizer(should_color(no_color_flag=no_color))
    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume)

    def handler(session: SyncSession) -> None:
        print("claudestream repl")
        print("Type your prompts. Ctrl-D to exit.\n")
        model_shown = False
        while True:
            try:
                prompt = input("> ")
            except EOFError:
                print("\nBye.")
                break
            if not prompt.strip():
                continue
            for event in session.send(prompt):
                if isinstance(event, AssistantText):
                    sys.stdout.write(event.text)
                    sys.stdout.flush()
                elif isinstance(event, ToolUse):
                    print(f"\n[tool: {color.bold(event.name)}]")
                elif isinstance(event, ToolResult):
                    content = event.content if isinstance(event.content, str) else str(event.content)
                    if len(content) > 500:
                        content = content[:500] + "..."
                    print(f"[result: {content}]")
                elif isinstance(event, Result):
                    if footer:
                        print(color.cyan(f"\n[cost: ${event.total_cost_usd:.4f}]"), file=sys.stderr)
                elif isinstance(event, Thinking):
                    print(color.dim("[thinking...]"), file=sys.stderr)
                elif isinstance(event, ApiRetry):
                    print(color.yellow(f"[retry {event.attempt}/{event.max_retries}: {event.error}]"), file=sys.stderr)
                elif isinstance(event, RateLimit):
                    print(color.yellow(f"[rate limit: {event.status}]"), file=sys.stderr)
                elif isinstance(event, PermissionRequest):
                    print(color.yellow(f"[permission: {event.tool_name}]"), file=sys.stderr)
                elif isinstance(event, (StreamDelta, SystemInit, CompactBoundary, McpRequest, UnknownEvent)):
                    pass
            if not model_shown and session.model_name:
                print(color.dim(f"Connected: {session.model_name}"), file=sys.stderr)
                model_shown = True
            print()

    return _run_with_session(config, handler, color)


# --- agent group ---

agent_group = app.group("agent", help="Manage and run agents defined in .agent.json files. Agent definitions declare a model, prompt template, allowed tools with input schemas, sandbox permissions, and budget limits (cost, turns, tokens). Use subcommands to validate configurations, run agents against prompts, and inspect metadata.")


@agent_group.command("run", help="Load an agent definition and run it with the given prompt. Accepts a path to a .agent.json file or a bare agent name (resolved from .claudestream/agents/). The definition specifies the model, a prompt template with {variable} placeholders, tool schemas, sandbox policy, and budget constraints. Use --var key=value to substitute template variables. Use --model to override the model declared in the definition.")
@strictcli.arg("definition", help="Agent name or path to .agent.json file")
@strictcli.arg("prompt", help="User message to send to the agent")
@strictcli.flag("var", type=str, help="Variable in key=value format (repeatable)", default=[], repeatable=True)
@strictcli.flag("model", type=str, help="Model override", short="m", default="")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("cwd", type=str, help="Working directory", default="")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("no-color", type=bool, default=False, help="Disable colored output")
def cmd_agent_run(
    definition: str,
    prompt: str,
    var: list[str],
    model: str,
    profile: str,
    cwd: str = "",
    footer: bool = True,
    no_color: bool = False,
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))

    # Parse variables from --var key=value flags
    variables: dict[str, str] = {}
    for v in var:
        if "=" not in v:
            print(color.red(f"error: --var must be key=value, got: {v!r}"), file=sys.stderr)
            return 1
        key, value = v.split("=", 1)
        variables[key] = value

    try:
        agent_def = load_agent(definition)
    except Exception as e:
        print(color.red(f"error: failed to load agent definition: {e}"), file=sys.stderr)
        return 1

    if not model and not agent_def.model:
        print("Error: no model specified. Use --model or set 'model' in the agent definition.", file=sys.stderr)
        return 1

    try:
        with invoke_agent_sync(
            agent_def,
            profile,
            variables=variables or None,
            model=model or None,
            cwd=cwd or None,
        ) as session:
            _stream_events(session, prompt, footer, color)
    except ValueError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


@agent_group.command("list", help="List available agents from .claudestream/agents/")
@strictcli.flag("cwd", type=str, default="", help="Working directory")
def cmd_agent_list(cwd: str = "") -> int | None:
    agents = discover_agents(cwd or None)
    if not agents:
        print("No agents found in .claudestream/agents/")
        return None
    # Determine column widths
    name_w = max(len(a.name) for a in agents)
    ver_w = max(len(a.version) for a in agents)
    # Print header
    print(f"{'NAME':<{name_w}}  {'VERSION':<{ver_w}}  DESCRIPTION")
    for a in agents:
        desc = a.description or ""
        print(f"{a.name:<{name_w}}  {a.version:<{ver_w}}  {desc}")
    return None


@agent_group.command("info", help="Display agent definition details")
@strictcli.arg("name", help="Agent name or path")
def cmd_agent_info(name: str) -> int | None:
    try:
        agent = load_agent(name)
    except (FileNotFoundError, Exception) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"Name:        {agent.name}")
    print(f"Version:     {agent.version}")
    if agent.description:
        print(f"Description: {agent.description}")
    if agent.model:
        print(f"Model:       {agent.model}")
    if agent.budget:
        parts = []
        if agent.budget.max_cost_usd is not None:
            parts.append(f"max_cost_usd={agent.budget.max_cost_usd}")
        if agent.budget.max_turns is not None:
            parts.append(f"max_turns={agent.budget.max_turns}")
        if agent.budget.max_tokens is not None:
            parts.append(f"max_tokens={agent.budget.max_tokens}")
        if parts:
            print(f"Budget:      {', '.join(parts)}")
    if agent.sandbox:
        print(f"Sandbox:     tools={agent.sandbox.tools}")
    if agent.tools:
        print("Tools:")
        for t in agent.tools:
            print(f"  - {t.name}: {t.description}")
    if agent.mcp:
        print(f"MCP:         config_files={agent.mcp.config_files}")
    if agent.stream:
        print(f"Stream:      verbose={agent.stream.verbose}")
    return None


@agent_group.command("validate", help="Validate an agent definition")
@strictcli.arg("name", help="Agent name or path")
def cmd_agent_validate(name: str) -> int | None:
    try:
        agent = load_agent(name)
    except (FileNotFoundError, Exception) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    # Validate budget values are non-negative
    if agent.budget:
        if agent.budget.max_cost_usd is not None and agent.budget.max_cost_usd < 0:
            print("error: budget.max_cost_usd must be non-negative", file=sys.stderr)
            return 1
        if agent.budget.max_turns is not None and agent.budget.max_turns < 0:
            print("error: budget.max_turns must be non-negative", file=sys.stderr)
            return 1
        if agent.budget.max_tokens is not None and agent.budget.max_tokens < 0:
            print("error: budget.max_tokens must be non-negative", file=sys.stderr)
            return 1
    # Basic tool schema validation
    if agent.tools:
        for t in agent.tools:
            schema = t.input_schema
            if not isinstance(schema, dict):
                print(f"error: tool '{t.name}' input_schema must be an object", file=sys.stderr)
                return 1
    print(f"Valid: {agent.name} v{agent.version}")
    return None


# --- Helpers ---

class EventPrinter:
    """Stateful event printer that deduplicates AssistantText against StreamDelta."""

    def __init__(
        self,
        footer: bool = True,
        color: Colorizer | None = None,
        tool_result_truncation: int = 500,
        thinking_preview_length: int = 100,
    ) -> None:
        self._streamed_text: str = ""
        self._footer = footer
        self._color = color or Colorizer(use_color=False)
        self._tool_result_truncation = tool_result_truncation
        self._thinking_preview_length = thinking_preview_length

    def print_event(self, event: Event) -> None:
        """Pretty-print an event to stdout, deduplicating AssistantText."""
        c = self._color
        if isinstance(event, StreamDelta):
            if event.text:
                self._streamed_text += event.text
                sys.stdout.write(event.text)
                sys.stdout.flush()
        elif isinstance(event, AssistantText):
            # Print only if the text differs from what StreamDelta already printed
            if event.text != self._streamed_text:
                sys.stdout.write(event.text)
                sys.stdout.flush()
        elif isinstance(event, ToolUse):
            print(f"\n--- Tool: {c.bold(event.name)} ---")
            print(json.dumps(event.input, indent=2))
        elif isinstance(event, ToolResult):
            content = event.content if isinstance(event.content, str) else str(event.content)
            limit = self._tool_result_truncation
            if len(content) > limit:
                content = content[:limit] + "..."
            print(f"--- Result ---\n{content}")
        elif isinstance(event, Thinking):
            limit = self._thinking_preview_length
            preview = event.text[:limit] + "..." if len(event.text) > limit else event.text
            print(c.dim(f"[thinking: {preview}]"))
        elif isinstance(event, Result):
            if self._footer:
                print(c.cyan(f"\n--- Done ({event.duration_ms:.0f}ms, ${event.total_cost_usd:.4f}) ---"), file=sys.stderr)
            self._streamed_text = ""
        elif isinstance(event, ApiRetry):
            print(c.yellow(f"[retry {event.attempt}/{event.max_retries}: {event.error}]"), file=sys.stderr)
        elif isinstance(event, PermissionRequest):
            print(c.yellow(f"[permission needed: {event.tool_name}]"), file=sys.stderr)
        elif isinstance(event, RateLimit):
            print(c.yellow(f"[rate limit: {event.status}]"), file=sys.stderr)
        elif isinstance(event, (SystemInit, CompactBoundary, McpRequest, UnknownEvent)):
            pass


def _print_json(event: Event) -> None:
    """Print an event as a JSON line."""
    d = msgspec.to_builtins(event)
    print(json.dumps(d))


def main() -> None:
    app.run()
