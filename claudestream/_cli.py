"""CLI entry point for claudestream."""

from __future__ import annotations

import json
import sys

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
    StreamDelta,
    SyncSession,
    Thinking,
    ToolResult,
    ToolUse,
    ApiRetry,
    SystemInit,
    UnknownEvent,
    allow_all,
)
from claudestream._color import Colorizer, should_color

from importlib.metadata import version as _pkg_version

app = strictcli.App(
    name="claudestream",
    version=_pkg_version("claudestream"),
    help="Stream Claude Code's JSON protocol",
)


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
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))
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
    policy = allow_all() if skip_permissions else None
    try:
        printer = EventPrinter(footer=footer, color=color)
        with SyncSession(
            model=model,
            cwd=cwd or None,
            policy=policy,
            profile=profile,
            system_prompt=system_prompt or None,
        ) as session:
            for event in session.send(prompt, raw=raw):
                if json_output:
                    _print_json(event)
                else:
                    printer.print_event(event)
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


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
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))
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
    policy = allow_all() if skip_permissions else None
    try:
        streamed_text = ""
        with SyncSession(
            model=model,
            cwd=cwd or None,
            policy=policy,
            profile=profile,
            system_prompt=system_prompt or None,
        ) as session:
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
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


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
) -> int | None:
    color = Colorizer(should_color(no_color_flag=no_color))
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
    policy = allow_all() if skip_permissions else None
    try:
        with SyncSession(
            model=model,
            cwd=cwd or None,
            policy=policy,
            profile=profile,
            system_prompt=system_prompt or None,
        ) as session:
            for event in session.send(prompt, raw=True):
                _print_json(event)
                if footer and isinstance(event, Result):
                    print(color.cyan(f"--- Done ({event.duration_ms:.0f}ms, ${event.total_cost_usd:.4f}) ---"), file=sys.stderr)
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


# --- repl command ---

@app.command("repl", help="Interactive multi-turn REPL")
@strictcli.flag("model", type=str, help="Model to use", short="m")
@strictcli.flag("cwd", type=str, default="", help="Working directory for Claude")
@strictcli.flag("skip-permissions", type=bool, default=False, help="Skip all permission prompts")
@strictcli.flag("profile", type=str, help="claudewheel profile to use")
@strictcli.flag("footer", type=bool, default=True, help="Show cost and timing on stderr")
@strictcli.flag("system-prompt", type=str, default="", help="System prompt for Claude", short="s")
@strictcli.flag("no-color", type=bool, default=False, help="Disable colored output")
def cmd_repl(
    model: str,
    profile: str,
    cwd: str = "",
    skip_permissions: bool = False,
    footer: bool = True,
    system_prompt: str = "",
    no_color: bool = False,
) -> None:
    color = Colorizer(should_color(no_color_flag=no_color))
    policy = allow_all() if skip_permissions else None
    try:
        with SyncSession(
            model=model,
            cwd=cwd or None,
            policy=policy,
            profile=profile,
            system_prompt=system_prompt or None,
        ) as session:
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
    except ClaudeStreamError as e:
        print(color.red(f"error: {e}"), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 1


# --- Helpers ---

class EventPrinter:
    """Stateful event printer that deduplicates AssistantText against StreamDelta."""

    def __init__(self, footer: bool = True, color: Colorizer | None = None) -> None:
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
            if len(content) > 500:
                content = content[:500] + "..."
            print(f"--- Result ---\n{content}")
        elif isinstance(event, Thinking):
            preview = event.text[:100] + "..." if len(event.text) > 100 else event.text
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
    import dataclasses
    d = dataclasses.asdict(event)
    print(json.dumps(d))


def main() -> None:
    app.run()
