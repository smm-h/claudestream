"""Async session for Claude Code stream-json protocol."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Callable

from claudestream.events import (
    Event,
    McpRequest,
    PermissionRequest,
    Result,
    SystemInit,
    UnknownEvent,
)
from claudestream.messages import AllowPermission, DenyPermission, UserMessage
from claudestream.policy import Allow, Deny, Policy, policy_to_flags
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
        *,
        model: str | None = None,
        cwd: str | None = None,
        binary: str | None = None,
        policy: Policy | None = None,
        system_prompt: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        profile: str | None = None,
    ):
        self._binary = find_binary(binary)
        self._policy = policy
        self._callbacks: dict[type, list[Callable]] = {}
        self._active_turn = False
        self._last_result: Result | None = None

        # Convert policy to CLI flags, then route them to ProcessConfig fields
        policy_flags = policy_to_flags(policy)

        permission_mode: str | None = None
        permission_prompt_tool: str | None = None
        allowed_tools: list[str] = []
        skip_permissions = False

        remaining_flags: list[str] = []
        i = 0
        while i < len(policy_flags):
            flag = policy_flags[i]
            if flag == "--dangerously-skip-permissions":
                skip_permissions = True
                i += 1
            elif flag == "--permission-mode":
                permission_mode = policy_flags[i + 1]
                i += 2
            elif flag == "--permission-prompt-tool":
                permission_prompt_tool = policy_flags[i + 1]
                i += 2
            elif flag == "--allowedTools":
                allowed_tools = policy_flags[i + 1].split(",")
                i += 2
            else:
                remaining_flags.append(flag)
                i += 1

        all_extra = list(extra_args or [])
        if skip_permissions:
            all_extra.append("--dangerously-skip-permissions")
        all_extra.extend(remaining_flags)

        merged_env = dict(env or {})
        if profile:
            from claudewheel.profile import resolve_profile
            merged_env.update(resolve_profile(profile))

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
        log.debug("process started, skipping SystemInit wait (captured on first send)")

    async def close(self) -> None:
        """Shut down the session and kill the subprocess."""
        await self._process_mgr.close()

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

    async def _read_turn(self, *, raw: bool) -> AsyncIterator[Event]:
        """Read events for a single turn until Result is received."""
        if not self._process_mgr.is_alive:
            raise ClaudeStreamError(
                "Claude subprocess is not running",
                exit_code=None,
            )

        async for event in read_events(self._process_mgr.stdout):
            # Capture SystemInit (sent after first user message)
            if isinstance(event, SystemInit):
                self._session_id = event.session_id
                self._model_name = event.model
                self._tools = list(event.tools)
                log.debug(
                    "session started: id=%s model=%s tools=%d",
                    self._session_id,
                    self._model_name,
                    len(self._tools),
                )
                continue  # don't yield SystemInit to consumer

            # Handle permission requests via policy
            if isinstance(event, PermissionRequest):
                handled = await self._handle_permission(event)
                if handled:
                    continue

            # Flatten or pass through
            if raw:
                events_to_yield = [event]
            else:
                events_to_yield = flatten_event(event)

            for evt in events_to_yield:
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

    async def _handle_permission(self, request: PermissionRequest) -> bool:
        """Apply policy to a permission request. Returns True if handled."""
        if self._policy is None:
            return False

        decision = self._policy.decide(request.tool_name, request.tool_input)

        if decision is None:
            return False

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
