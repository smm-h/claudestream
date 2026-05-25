"""Async session for Claude Code stream-json protocol."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Callable

from claudestream.events import (
    ApiRetry,
    AssistantMessage,
    Event,
    McpRequest,
    PermissionRequest,
    RateLimit,
    Result,
    SystemInit,
    TextBlock,
    Thinking,
    ToolResult,
    ToolUse,
    UnknownEvent,
)
from claudestream.messages import AllowPermission, DenyPermission, UserMessage
from claudestream.policy import Allow, Deny, Sandbox, sandbox_to_flags, sandbox_decide
from claudestream._process import ProcessConfig, ProcessManager, find_binary, check_version
from claudestream._protocol import flatten_event, read_events, write_message

log = logging.getLogger("claudestream")


class ClaudeStreamError(Exception):
    """Raised when the Claude Code subprocess fails."""

    def __init__(self, message: str, exit_code: int | None = None):
        super().__init__(message)
        self.exit_code = exit_code


class AsyncSession:
    """Async session managing a Claude Code subprocess.

    Usage::

        async with AsyncSession(model="sonnet") as session:
            async for event in session.send("hello"):
                print(event)
    """

    def __init__(
        self,
        model: str,
        profile: str,
        *,
        cwd: str | None = None,
        binary: str | None = None,
        sandbox: Sandbox | None = None,
        system_prompt: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self._binary = find_binary(binary)
        self._sandbox = sandbox
        self._callbacks: dict[type, list[Callable]] = {}
        self._active_turn = False
        self._last_result: Result | None = None
        self._cancelled = False

        # Convert sandbox to CLI flags, then route them to ProcessConfig fields
        sandbox_flags = sandbox_to_flags(sandbox)

        permission_mode: str | None = None
        permission_prompt_tool: str | None = None
        allowed_tools: list[str] = []
        skip_permissions = False

        remaining_flags: list[str] = []
        i = 0
        while i < len(sandbox_flags):
            flag = sandbox_flags[i]
            if flag == "--dangerously-skip-permissions":
                skip_permissions = True
                i += 1
            elif flag == "--permission-mode":
                permission_mode = sandbox_flags[i + 1]
                i += 2
            elif flag == "--permission-prompt-tool":
                permission_prompt_tool = sandbox_flags[i + 1]
                i += 2
            elif flag == "--allowedTools":
                allowed_tools = sandbox_flags[i + 1].split(",")
                i += 2
            else:
                remaining_flags.append(flag)
                i += 1

        all_extra = list(extra_args or [])
        if skip_permissions:
            all_extra.append("--dangerously-skip-permissions")
        all_extra.extend(remaining_flags)

        from claudewheel.profile import resolve_profile
        merged_env = {}
        merged_env.update(resolve_profile(profile))
        merged_env.update(env or {})

        self._process_mgr = ProcessManager(ProcessConfig(
            binary=self._binary,
            cwd=cwd,
            model=model,
            system_prompt=system_prompt,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            permission_prompt_tool=permission_prompt_tool,
            extra_args=all_extra,
            env=merged_env or None,
        ))

        # Session metadata (populated from SystemInit)
        self._session_id: str | None = None
        self._model_name: str | None = None
        self._tools: list[str] = []
        self._claude_version: str | None = None

    # --- Properties ---

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def model_name(self) -> str | None:
        return self._model_name

    @property
    def tools(self) -> list[str]:
        return self._tools

    @property
    def claude_version(self) -> str | None:
        return self._claude_version

    @property
    def last_result(self) -> Result | None:
        return self._last_result

    @property
    def stderr_lines(self) -> list[str]:
        return self._process_mgr.stderr_lines

    # --- Context manager ---

    async def __aenter__(self) -> AsyncSession:
        await self._start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def _start(self) -> None:
        """Start the subprocess. SystemInit is captured during first send()."""
        self._claude_version = await check_version(self._binary)

        await self._process_mgr.start()

        # With --input-format stream-json, the Claude CLI does NOT send
        # SystemInit until the first user message. We skip the blocking
        # read and capture SystemInit during the first send() instead.
        log.info("process started, skipping SystemInit wait (captured on first send)")

    async def close(self) -> None:
        """Shut down the session and kill the subprocess."""
        await self._process_mgr.close()

    # --- Cancel ---

    async def cancel(self, force: bool = False) -> None:
        """Cancel the current operation.

        Args:
            force: If False, close stdin (graceful). If True, terminate subprocess.
        """
        self._cancelled = True
        if force:
            await self._process_mgr.close()
        else:
            proc = self._process_mgr._process
            if proc and proc.stdin:
                proc.stdin.close()

    # --- Sending messages ---

    async def send(self, prompt: str, *, raw: bool = False) -> AsyncIterator[Event]:
        """Send a message and yield events until the turn completes.

        Args:
            prompt: The message to send.
            raw: If True, yield raw protocol events (AssistantMessage,
                 ToolResultMessage). If False (default), yield flattened
                 convenience events (AssistantText, ToolUse, etc.).

        Yields:
            Event objects until a Result event is received.

        Raises:
            RuntimeError: If called while a previous turn is still active.
            ClaudeStreamError: If the subprocess dies unexpectedly.
        """
        self._cancelled = False

        if self._active_turn:
            raise RuntimeError(
                "Cannot send while a previous turn is active. "
                "Drain the event iterator or wait for the Result event."
            )

        self._active_turn = True
        self._last_result = None

        try:
            msg = UserMessage(content=prompt, session_id=self._session_id or "")
            await write_message(self._process_mgr.stdin, msg)

            async for event in self._read_turn(raw=raw):
                yield event
        finally:
            self._active_turn = False

    async def _read_turn(
        self, *, raw: bool, _health_timeout: float = 30.0,
    ) -> AsyncIterator[Event]:
        """Read events for a single turn until Result is received."""
        if not self._process_mgr.is_alive:
            raise ClaudeStreamError(
                "Claude subprocess is not running",
                exit_code=None,
            )

        # Check if process already exited before we start reading
        if self._process_mgr._process is not None and self._process_mgr._process.returncode is not None:
            raise ClaudeStreamError(
                "Claude subprocess already exited",
                exit_code=self._process_mgr._process.returncode,
            )

        # Health probe: warn if no events arrive within timeout
        _first_event_received = False

        async def _health_check() -> None:
            await asyncio.sleep(_health_timeout)
            if not _first_event_received:
                log.warning("No events received after %.0fs — subprocess may be stuck", _health_timeout)

        health_task = asyncio.ensure_future(_health_check())

        try:
            async for event in read_events(self._process_mgr.stdout):
                if self._cancelled:
                    raise ClaudeStreamError("Session cancelled")

                if not _first_event_received:
                    _first_event_received = True
                    health_task.cancel()

                # Capture SystemInit (sent after first user message)
                if isinstance(event, SystemInit):
                    self._session_id = event.session_id
                    self._model_name = event.model
                    self._tools = list(event.tools)
                    log.info(
                        "session started: id=%s model=%s tools=%d",
                        self._session_id,
                        self._model_name,
                        len(self._tools),
                    )

                # Handle permission requests via sandbox
                if isinstance(event, PermissionRequest):
                    await self._handle_permission(event)

                # Per-type event logging (before flatten)
                if isinstance(event, Thinking):
                    log.info("event: Thinking (%d chars)", len(event.text))
                elif isinstance(event, Result):
                    log.info(
                        "event: Result (%.0fms, $%.4f, stop=%s)",
                        event.duration_ms,
                        event.total_cost_usd or 0,
                        event.stop_reason,
                    )
                elif isinstance(event, ApiRetry):
                    log.info(
                        "event: ApiRetry (attempt %d/%d, error=%s)",
                        event.attempt,
                        event.max_retries,
                        event.error,
                    )
                elif isinstance(event, RateLimit):
                    log.info(
                        "event: RateLimit (status=%s, resets_at=%s)",
                        event.status,
                        event.resets_at,
                    )
                elif isinstance(event, UnknownEvent):
                    if event.type == "system":
                        log.debug("event: Unknown system subtype (%s)", list(event.raw.keys()))
                    else:
                        log.warning("event: Unknown (%s)", list(event.raw.keys()))

                # Detect authentication errors
                if isinstance(event, AssistantMessage):
                    error_lower = (event.error or "").lower()
                    content_lower = " ".join(
                        block.text for block in event.content
                        if isinstance(block, TextBlock) and block.text
                    ).lower()
                    is_auth_error = (
                        any(p in error_lower for p in ("not logged in", "invalid authentication", "401"))
                        or any(p in content_lower for p in ("not logged in", "invalid authentication"))
                    )
                    if is_auth_error:
                        raise ClaudeStreamError(
                            "Authentication failed. Run `claude /login` to authenticate, "
                            "or check that your profile credentials are valid."
                        )

                # Flatten or pass through
                if raw:
                    events_to_yield = [event]
                else:
                    events_to_yield = flatten_event(event)

                for evt in events_to_yield:
                    # Log flattened events
                    if isinstance(evt, ToolUse):
                        log.info("event: ToolUse (%s)", evt.name)
                    elif isinstance(evt, ToolResult):
                        log.info("event: ToolResult (%d chars)", len(str(evt.content)))

                    # Fire callbacks before yielding
                    for cb in self._callbacks.get(type(evt), []):
                        cb(evt)
                    yield evt

                # Turn completes on Result
                if isinstance(event, Result):
                    self._last_result = event
                    return

            # stdout closed without a Result event
            rc = self._process_mgr._process.returncode if self._process_mgr._process else None
            raise ClaudeStreamError(
                "Claude subprocess closed stdout without sending a Result event",
                exit_code=rc,
            )
        finally:
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

    async def _handle_permission(self, request: PermissionRequest) -> bool:
        """Apply sandbox rules to a permission request. Returns True if handled."""
        if self._sandbox is None:
            return False

        cwd = self._process_mgr.config.cwd or "."
        decision = sandbox_decide(self._sandbox, request.tool_name, request.tool_input, cwd)

        if isinstance(decision, Allow):
            updated = decision.updated_input if decision.updated_input else request.tool_input
            msg = AllowPermission(
                request_id=request.request_id,
                updated_input=updated,
            )
        else:
            msg = DenyPermission(
                request_id=request.request_id,
                message=decision.message,
            )

        await write_message(self._process_mgr.stdin, msg)
        return True

    # --- Callbacks ---

    def on(self, event_type: type[Event], handler: Callable[[Any], None]) -> None:
        """Register a callback for a specific event type.

        The callback fires during iteration, before the event is yielded.
        """
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(handler)

    # --- Permission response (for consumer-handled requests) ---

    async def respond_allow(self, request_id: str, updated_input: dict) -> None:
        """Allow a permission request that was surfaced to the consumer."""
        msg = AllowPermission(request_id=request_id, updated_input=updated_input)
        await write_message(self._process_mgr.stdin, msg)

    async def respond_deny(self, request_id: str, message: str = "Denied by user") -> None:
        """Deny a permission request that was surfaced to the consumer."""
        msg = DenyPermission(request_id=request_id, message=message)
        await write_message(self._process_mgr.stdin, msg)
