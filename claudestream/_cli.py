"""CLI entry point for claudestream."""

from __future__ import annotations

import json
import sys

import strictcli

from claudestream import (
    AssistantMessage,
    AssistantText,
    AsyncSession,
    ClaudeStreamError,
    Event,
    PermissionRequest,
    RateLimit,
    Result,
    StreamDelta,
    SyncSession,
    Thinking,
    ToolResult,
    ToolUse,
    ToolResultMessage,
    ApiRetry,
    SystemInit,
    UnknownEvent,
    allow_all,
)

from importlib.metadata import version as _pkg_version

app = strictcli.App(
    name="claudestream",
    version=_pkg_version("claudestream"),
    help="Stream Claude Code's JSON protocol",
)


# --- send command ---

@app.command("send", help="Send a prompt and display the response")
@strictcli.arg("prompt", help="The prompt to send")
@strictcli.flag("model", type=str, default="", help="Model to use (e.g. sonnet, opus)", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("raw", type=bool, default=False, help="Show raw protocol events instead of flattened")
@strictcli.flag("json-output", type=bool, default=False, help="Output events as JSON lines")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, default="", help="claudewheel profile to use")
def cmd_send(
    prompt: str,
    model: str = "",
    cwd: str = "",
    raw: bool = False,
    json_output: bool = False,
    skip_permissions: bool = False,
    profile: str = "",
) -> int | None:
    policy = allow_all() if skip_permissions else None
    try:
        with SyncSession(
            model=model or None,
            cwd=cwd or None,
            policy=policy,
            profile=profile or None,
        ) as session:
            for event in session.send(prompt, raw=raw):
                if json_output:
                    _print_json(event)
                else:
                    _print_event(event)
    except ClaudeStreamError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


# --- stream command ---

@app.command("stream", help="Stream a prompt with real-time token output")
@strictcli.arg("prompt", help="The prompt to send")
@strictcli.flag("model", type=str, default="", help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, default="", help="claudewheel profile to use")
def cmd_stream(
    prompt: str,
    model: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    profile: str = "",
) -> int | None:
    policy = allow_all() if skip_permissions else None
    try:
        with SyncSession(
            model=model or None,
            cwd=cwd or None,
            policy=policy,
            profile=profile or None,
        ) as session:
            for event in session.send(prompt):
                if isinstance(event, StreamDelta) and event.text:
                    sys.stdout.write(event.text)
                    sys.stdout.flush()
                elif isinstance(event, AssistantText):
                    # Fallback if streaming deltas aren't available
                    pass
                elif isinstance(event, Result):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
    except ClaudeStreamError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


# --- events command ---

@app.command("events", help="Debug: show all raw protocol events")
@strictcli.arg("prompt", help="The prompt to send")
@strictcli.flag("model", type=str, default="", help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, default="", help="claudewheel profile to use")
def cmd_events(
    prompt: str,
    model: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    profile: str = "",
) -> int | None:
    policy = allow_all() if skip_permissions else None
    try:
        with SyncSession(
            model=model or None,
            cwd=cwd or None,
            policy=policy,
            profile=profile or None,
        ) as session:
            for event in session.send(prompt, raw=True):
                _print_json(event)
    except ClaudeStreamError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


# --- repl command ---

@app.command("repl", help="Interactive multi-turn REPL")
@strictcli.flag("model", type=str, default="", help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, default="", help="claudewheel profile to use")
def cmd_repl(
    model: str = "",
    cwd: str = "",
    skip_permissions: bool = False,
    profile: str = "",
) -> None:
    policy = allow_all() if skip_permissions else None
    try:
        with SyncSession(
            model=model or None,
            cwd=cwd or None,
            policy=policy,
            profile=profile or None,
        ) as session:
            print(f"claudestream repl (model: {session.model_name or 'default'})")
            print("Type your prompts. Ctrl-D to exit.\n")
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
                        print(f"\n[tool: {event.name}]")
                    elif isinstance(event, ToolResult):
                        content = event.content if isinstance(event.content, str) else str(event.content)
                        if len(content) > 200:
                            content = content[:200] + "..."
                        print(f"[result: {content}]")
                    elif isinstance(event, Result):
                        print(f"\n[cost: ${event.total_cost_usd:.4f}]")
                print()
    except ClaudeStreamError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")


# --- Helpers ---

def _print_event(event: Event) -> None:
    """Pretty-print an event to stdout."""
    if isinstance(event, AssistantText):
        # StreamDelta already printed this text incrementally; skip to avoid duplication
        pass
    elif isinstance(event, ToolUse):
        print(f"\n--- Tool: {event.name} ---")
        print(json.dumps(event.input, indent=2))
    elif isinstance(event, ToolResult):
        content = event.content if isinstance(event.content, str) else str(event.content)
        if len(content) > 500:
            content = content[:500] + "..."
        print(f"--- Result ---\n{content}")
    elif isinstance(event, Thinking):
        print(f"[thinking: {event.text[:100]}...]")
    elif isinstance(event, Result):
        print(f"\n--- Done ({event.duration_ms:.0f}ms, ${event.total_cost_usd:.4f}) ---")
    elif isinstance(event, ApiRetry):
        print(f"[retry {event.attempt}/{event.max_retries}: {event.error}]", file=sys.stderr)
    elif isinstance(event, PermissionRequest):
        print(f"[permission needed: {event.tool_name}]", file=sys.stderr)
    elif isinstance(event, StreamDelta):
        if event.text:
            sys.stdout.write(event.text)
            sys.stdout.flush()


def _print_json(event: Event) -> None:
    """Print an event as a JSON line."""
    import dataclasses
    d = dataclasses.asdict(event)
    print(json.dumps(d))


def main() -> None:
    app.run()
