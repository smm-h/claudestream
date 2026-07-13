"""Command-line interface entry point for claudestream, providing send, listen, and agent commands for interacting with Claude Code."""

from __future__ import annotations

import json
import sys
from typing import Any

import msgspec
import strictcli

from claudestream import (
    AssistantText,
    BudgetThreshold,
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
from claudestream._options import validate_budget
from claudestream._color import Colorizer, should_color
from claudestream._process import MINIMUM_CLAUDE_VERSION, find_binary, check_version, _version_lt

def _get_version() -> str:
    """Read version from pyproject.toml (editable installs) or fall back to package metadata."""
    import tomllib
    from pathlib import Path
    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    if pyproject.exists():
        with open(pyproject, "rb") as f:
            return tomllib.load(f)["project"]["version"]
    from importlib.metadata import version
    return version("claudestream")

app = strictcli.App(
    name="claudestream",
    version=_get_version(),
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
    from_pr: str = "",
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
        from_pr=from_pr or None,
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
        elif isinstance(event, BudgetThreshold):
            print(f"[threshold: {event.metric} {event.threshold} crossed at {event.current_value}]", file=sys.stderr)
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
@strictcli.flag("color", type=bool, default=True, help="Enable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
@strictcli.flag("from-pr", type=str, default="", help="Resume from a PR")
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
    color: bool = True,
    resume: str = "",
    from_pr: str = "",
) -> int | None:
    color = Colorizer(should_color(color_flag=color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume, from_pr)
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
@strictcli.flag("color", type=bool, default=True, help="Enable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
@strictcli.flag("from-pr", type=str, default="", help="Resume from a PR")
def cmd_stream(
    prompt: str = "",
    model: str = "",
    profile: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    stdin: bool = False,
    color: bool = True,
    resume: str = "",
    from_pr: str = "",
) -> int | None:
    color = Colorizer(should_color(color_flag=color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume, from_pr)

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
@strictcli.flag("color", type=bool, default=True, help="Enable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
@strictcli.flag("from-pr", type=str, default="", help="Resume from a PR")
def cmd_events(
    prompt: str = "",
    model: str = "",
    profile: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    stdin: bool = False,
    color: bool = True,
    resume: str = "",
    from_pr: str = "",
) -> int | None:
    color = Colorizer(should_color(color_flag=color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume, from_pr)

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
@strictcli.flag("color", type=bool, default=True, help="Enable colored output")
@strictcli.flag("resume", type=str, default="", help="Resume a previous session by ID")
@strictcli.flag("from-pr", type=str, default="", help="Resume from a PR")
def cmd_repl(
    model: str,
    profile: str,
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    color: bool = True,
    resume: str = "",
    from_pr: str = "",
) -> None:
    color = Colorizer(should_color(color_flag=color))
    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, resume, from_pr)

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
                elif isinstance(event, BudgetThreshold):
                    print(f"[threshold: {event.metric} {event.threshold} crossed at {event.current_value}]", file=sys.stderr)
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
@strictcli.flag("var", type=str, help="Variable in key=value format (repeatable)", repeatable=True, unique=False)
@strictcli.flag("model", type=str, help="Model override", short="m", default="")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("cwd", type=str, help="Working directory", default="")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("color", type=bool, default=True, help="Enable colored output")
def cmd_agent_run(
    definition: str,
    prompt: str,
    var: list[str],
    model: str,
    profile: str,
    cwd: str = "",
    footer: bool = True,
    color: bool = True,
) -> int | None:
    color = Colorizer(should_color(color_flag=color))

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

    base_config = SessionConfig(
        model=model or agent_def.model or "",
        profile=profile,
        cwd=cwd or None,
    )

    try:
        with invoke_agent_sync(
            agent_def,
            base_config,
            variables=variables or None,
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


@agent_group.command("list", help="List available agents from .claudestream/agents/. Scans the agents directory in the working directory (or the directory specified by --cwd) and prints a table with each agent's name, schema version, and description. Use this to discover which agents are configured before running one with 'agent run'.")
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


@agent_group.command("info", help="Display agent definition details for a given agent name or path. Loads the .agent.json file, parses it, and prints every configured field: name, version, description, model, budget limits, sandbox policy, tool schemas, MCP server config, and stream options. Use this to inspect an agent's full configuration before invoking it.")
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
        if agent.budget.cost_thresholds:
            print(f"Cost thresholds: {agent.budget.cost_thresholds}")
        if agent.budget.turn_thresholds:
            print(f"Turn thresholds: {agent.budget.turn_thresholds}")
        if agent.budget.token_thresholds:
            print(f"Token thresholds: {agent.budget.token_thresholds}")
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


@agent_group.command("validate", help="Validate an agent definition by loading and checking its .agent.json file for structural and semantic correctness. Verifies that budget values are non-negative, the prompt template is non-empty, tool schemas are well-formed, and required fields are present. Reports specific errors on failure or prints a success confirmation.")
@strictcli.arg("name", help="Agent name or path")
def cmd_agent_validate(name: str) -> int | None:
    try:
        agent = load_agent(name)
    except (FileNotFoundError, Exception) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    # Validate budget thresholds are non-negative
    if agent.budget:
        try:
            validate_budget(agent.budget)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
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


# --- ask command ---

@app.command("ask", help="Send a prompt and print the response text")
@strictcli.arg("prompt", help="The prompt to send", required=False, default="")
@strictcli.flag("model", type=str, short="m", help="Model to use")
@strictcli.flag("profile", type=str, help="claudewheel profile")
@strictcli.flag("cwd", type=str, default="", help="Working directory")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("system-prompt", type=str, default="", short="s", help="System prompt")
@strictcli.flag("stdin", type=bool, default=False, help="Read prompt from stdin")
@strictcli.flag("json-output", type=bool, default=False, help="Output AskResult as JSON")
@strictcli.flag("color", type=bool, default=True, help="Enable colored output")
@strictcli.flag("from-pr", type=str, default="", help="Resume from a PR")
def cmd_ask(
    prompt: str = "",
    model: str = "",
    profile: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    system_prompt: str = "",
    stdin: bool = False,
    json_output: bool = False,
    color: bool = True,
    from_pr: str = "",
) -> int | None:
    color = Colorizer(should_color(color_flag=color))
    resolved = _resolve_prompt(prompt, stdin, color)
    if isinstance(resolved, int):
        return resolved
    prompt = resolved

    config = _build_config(model, profile, cwd, skip_permissions, system_prompt, from_pr=from_pr)

    try:
        with SyncSession(config) as session:
            result = session.ask(prompt)
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1

    if json_output:
        print(json.dumps(msgspec.to_builtins(result)))
    else:
        print(result.text)
    return None


# --- doctor command ---

@app.command("doctor", help="Check claudestream environment health")
@strictcli.flag("profile", type=str, default="", help="Profile to check")
def cmd_doctor(profile: str = "") -> int | None:
    import asyncio

    ok = True

    # 1. Binary found
    try:
        binary = find_binary()
        print(f"[ok] Binary found: {binary}")
    except FileNotFoundError as e:
        print(f"[FAIL] Binary not found: {e}")
        ok = False
        binary = None

    # 2. Version check
    if binary:
        version = asyncio.run(check_version(binary))
        if version:
            msg = f"[ok] Version: {version}"
            if _version_lt(version, MINIMUM_CLAUDE_VERSION):
                msg += f" (WARNING: below minimum {MINIMUM_CLAUDE_VERSION})"
                ok = False
            print(msg)
        else:
            print("[FAIL] Could not determine version")
            ok = False

    # 3. Profile resolution
    if profile:
        try:
            from claudewheel.profile import resolve_profile
            env_vars = resolve_profile(profile)
            print(f"[ok] Profile '{profile}': {len(env_vars)} env var(s) resolved")
        except Exception as e:
            print(f"[FAIL] Profile '{profile}': {e}")
            ok = False

    return 0 if ok else 1


# --- config command ---

@app.command("config", help="Show resolved configuration")
@strictcli.flag("profile", type=str, default="", help="Profile to show")
def cmd_config(profile: str = "") -> int | None:
    import asyncio

    # 1. Binary path
    try:
        binary = find_binary()
        print(f"Binary: {binary}")
    except FileNotFoundError as e:
        print(f"Binary: not found ({e})")
        binary = None

    # 2. Version
    if binary:
        version = asyncio.run(check_version(binary))
        print(f"Version: {version or 'unknown'}")

    # 3. Minimum supported version
    print(f"Minimum version: {MINIMUM_CLAUDE_VERSION}")

    # 4. Profile
    if profile:
        try:
            from claudewheel.profile import resolve_profile
            env_vars = resolve_profile(profile)
            print(f"Profile: {profile}")
            for key, value in sorted(env_vars.items()):
                print(f"  {key}={value}")
        except Exception as e:
            print(f"Profile: {profile} (error: {e})")

    return None


# --- Helpers ---

class EventPrinter:
    """Stateful event printer that deduplicates AssistantText against StreamDelta."""

    def __init__(
        self,
        footer: bool = True,
        color: Colorizer | None = None,
    ) -> None:
        self._streamed_text: str = ""
        self._footer = footer
        self._color = color or Colorizer(use_color=False)

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
            print(f"--- Result ---\n{content}")
        elif isinstance(event, Thinking):
            print(c.dim(f"[thinking: {event.text}]"))
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
        elif isinstance(event, BudgetThreshold):
            print(f"[threshold: {event.metric} {event.threshold} crossed at {event.current_value}]", file=sys.stderr)
        elif isinstance(event, (SystemInit, CompactBoundary, McpRequest, UnknownEvent)):
            pass


def _print_json(event: Event) -> None:
    """Print an event as a JSON line."""
    d = msgspec.to_builtins(event)
    print(json.dumps(d))


def main() -> None:
    app.run()
