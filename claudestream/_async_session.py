"""Async session manager for the Claude Code stream-json protocol, handling process lifecycle, event parsing, and permission callbacks."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Callable

from claudestream.events import (
    ApiRetry,
    AskResult,
    AssistantMessage,
    AssistantText,
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
from claudestream.messages import AllowPermission, DenyPermission, InitializeRequest, McpResponse, UserMessage
from claudestream._options import SessionConfig
from claudestream.policy import Allow, Deny, Sandbox, sandbox_decide
from claudestream._process import ProcessConfig, ProcessManager, find_binary, check_version
from claudestream._protocol import flatten_event, read_events, write_message
from claudestream._tools import Tool

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

    def __init__(self, config: SessionConfig):
        self._config = config
        self._binary = find_binary(config.binary)
        self._sandbox = config.sandbox
        self._callbacks: dict[type, list[Callable]] = {}
        self._active_turn = False
        self._last_result: Result | None = None
        self._cancelled = False
        self._turn_count: int = 0
        self._total_tokens: int = 0

        # User-defined tools, grouped by server name for MCP handling
        self._user_tools: list[Tool] = list(config.tools or [])
        self._tools_by_server: dict[str, list[Tool]] = {}
        for t in self._user_tools:
            self._tools_by_server.setdefault(t.server, []).append(t)

        self._process_mgr = ProcessManager(self._build_process_config())

        # Health timeout for _read_turn (default 30s, overridden by ProcessLimits)
        self._health_timeout: float = 30.0
        if config.process_limits is not None:
            self._health_timeout = config.process_limits.health_timeout

        # Session metadata (populated from SystemInit)
        self._session_id: str | None = None
        self._model_name: str | None = None
        self._tools: list[str] = []
        self._claude_version: str | None = None
        self._cwd: str = ""
        self._mcp_servers: list[str] = []
        self._permission_mode: str = ""

    def _build_process_config(self) -> ProcessConfig:
        """Build a ProcessConfig from the stored SessionConfig.

        This is the single mapping point between the user-facing config
        and the subprocess CLI flags.
        """
        config = self._config
        sandbox = config.sandbox

        # --- Sandbox → ProcessConfig fields (no flag round-trip) ---
        allowed_tools: list[str] = []
        bare = False
        dangerously_skip_permissions = False
        permission_prompt_tool: str | None = None

        if sandbox is not None:
            bare = sandbox.bare
            dangerously_skip_permissions = sandbox.skip_permissions
            if sandbox.tools is not None:
                allowed_tools = list(sandbox.tools)
            # Permission interception needed when we restrict tools or write paths
            if sandbox.tools is not None or sandbox.write_paths is not None:
                permission_prompt_tool = "stdio"

        # Add MCP wildcard patterns for each server with registered tools
        for server_name in self._tools_by_server:
            allowed_tools.append(f"mcp__{server_name}__*")

        # --- Option structs → ProcessConfig fields ---
        debug_enabled = False
        debug_filter: str | None = None
        debug_file: str | None = None
        if config.debug is not None:
            debug_enabled = config.debug.enabled
            debug_filter = config.debug.filter
            debug_file = config.debug.file

        mcp_config: list[str] = []
        strict_mcp_config = False
        if config.mcp is not None:
            mcp_config = list(config.mcp.config_files)
            strict_mcp_config = config.mcp.strict

        plugin_dirs: list[str] = []
        plugin_urls: list[str] = []
        if config.plugins is not None:
            plugin_dirs = list(config.plugins.dirs)
            plugin_urls = list(config.plugins.urls)

        verbose = True
        include_partial_messages = True
        include_hook_events = False
        replay_user_messages = False
        exclude_dynamic_prompt_sections = False
        if config.stream is not None:
            verbose = config.stream.verbose
            include_partial_messages = config.stream.include_partial_messages
            include_hook_events = config.stream.include_hook_events
            replay_user_messages = config.stream.replay_user_messages
            exclude_dynamic_prompt_sections = config.stream.exclude_dynamic_prompt_sections

        buffer_limit = 16_777_216
        shutdown_timeout = 5.0
        if config.process_limits is not None:
            buffer_limit = config.process_limits.buffer_limit
            shutdown_timeout = config.process_limits.shutdown_timeout

        max_budget_usd: float | None = None
        if config.budget is not None:
            max_budget_usd = config.budget.max_cost_usd

        # --- SessionResolution → ProcessConfig fields ---
        name: str | None = None
        session_id: str | None = None
        resume_session_id = config.resume_session_id
        continue_session = False
        fork_session = False
        if config.session_resolution is not None:
            name = config.session_resolution.name
            session_id = config.session_resolution.session_id
            if config.session_resolution.resume_session_id is not None:
                resume_session_id = config.session_resolution.resume_session_id
            continue_session = config.session_resolution.continue_last
            fork_session = config.session_resolution.fork

        # --- json_schema dict → json_schema_str ---
        json_schema_str: str | None = None
        if config.json_schema is not None:
            import json
            json_schema_str = json.dumps(config.json_schema)

        # --- Profile → env vars ---
        from claudewheel.profile import resolve_profile
        merged_env: dict[str, str] = {}
        merged_env.update(resolve_profile(config.profile))
        merged_env.update(config.env or {})

        return ProcessConfig(
            binary=self._binary,
            cwd=config.cwd,
            model=config.model,
            system_prompt=config.system_prompt,
            permission_mode=None,  # not exposed via SessionConfig; sandbox handles it
            allowed_tools=allowed_tools,
            permission_prompt_tool=permission_prompt_tool,
            resume_session_id=resume_session_id,
            extra_args=list(config.extra_args or []),
            env=merged_env or None,
            # String value flags
            effort=config.effort,
            json_schema_str=json_schema_str,
            fallback_model=config.fallback_model,
            name=name,
            setting_sources=config.setting_sources,
            settings=config.settings,
            debug_filter=debug_filter,
            debug_file=debug_file,
            agent=config.agent_name,
            agents_json=config.agents_json,
            from_pr=config.from_pr,
            session_id=session_id,
            # List flags
            betas=list(config.betas or []),
            add_dirs=list(config.add_dirs or []),
            builtin_tools=list(config.builtin_tools or []),
            file_specs=list(config.file_specs or []),
            mcp_config=mcp_config,
            plugin_dirs=plugin_dirs,
            plugin_urls=plugin_urls,
            # Bool flags
            bare=bare,
            brief=config.brief,
            continue_session=continue_session,
            fork_session=fork_session,
            no_session_persistence=config.no_persistence,
            strict_mcp_config=strict_mcp_config,
            include_hook_events=include_hook_events,
            replay_user_messages=replay_user_messages,
            exclude_dynamic_prompt_sections=exclude_dynamic_prompt_sections,
            debug=debug_enabled,
            verbose=verbose,
            include_partial_messages=include_partial_messages,
            dangerously_skip_permissions=dangerously_skip_permissions,
            # Float
            max_budget_usd=max_budget_usd,
            # Process-level tuning
            buffer_limit=buffer_limit,
            shutdown_timeout=shutdown_timeout,
            # Hooks
            hooks=config.hooks or {},
        )

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
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def total_tokens(self) -> int:
        return self._total_tokens

    @property
    def stderr_lines(self) -> list[str]:
        return self._process_mgr.stderr_lines

    @property
    def sandbox(self) -> Sandbox | None:
        return self._config.sandbox

    @property
    def user_tools(self) -> list[Tool]:
        return self._user_tools

    @property
    def is_alive(self) -> bool:
        return self._process_mgr.is_alive

    @property
    def active_turn(self) -> bool:
        return self._active_turn

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def process_pid(self) -> int | None:
        proc = self._process_mgr._process
        return proc.pid if proc else None

    @property
    def cwd(self) -> str:
        return self._cwd

    @property
    def mcp_servers(self) -> list[str]:
        return self._mcp_servers

    @property
    def permission_mode(self) -> str:
        return self._permission_mode

    @property
    def config(self) -> SessionConfig:
        return self._config

    # --- Context manager ---

    async def __aenter__(self) -> AsyncSession:
        await self._start()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.close()

    async def _start(self) -> None:
        """Start the subprocess. SystemInit is captured during first send()."""
        version_check_timeout = 2.0
        if self._config.process_limits is not None:
            version_check_timeout = self._config.process_limits.version_check_timeout
        self._claude_version = await check_version(self._binary, timeout=version_check_timeout)

        await self._process_mgr.start()

        # Send InitializeRequest to register SDK MCP servers and/or hooks
        hooks = self._config.hooks or {}
        if self._user_tools or hooks:
            server_names = list(self._tools_by_server.keys())
            init_req = InitializeRequest(sdk_mcp_servers=server_names, hooks=hooks)
            await write_message(self._process_mgr.stdin, init_req)

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

    async def send(self, prompt: str | list, *, raw: bool = False) -> AsyncIterator[Event]:
        """Send a message and yield events until the turn completes.

        Args:
            prompt: The message to send. Can be a plain string or a list of
                content blocks (dicts) for multimodal input.
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

        # Budget enforcement: check limits before starting a new turn
        budget = self._config.budget
        if budget is not None:
            if budget.max_turns is not None and self._turn_count >= budget.max_turns:
                raise ClaudeStreamError("Budget exceeded: max_turns limit reached")
            if budget.max_tokens is not None and self._total_tokens >= budget.max_tokens:
                raise ClaudeStreamError("Budget exceeded: max_tokens limit reached")

        self._active_turn = True
        self._last_result = None

        try:
            msg = UserMessage(content=prompt, session_id=self._session_id or "")
            await write_message(self._process_mgr.stdin, msg)

            async for event in self._read_turn(raw=raw, _health_timeout=self._health_timeout):
                yield event
        finally:
            self._active_turn = False

    async def ask(self, prompt: str | list) -> AskResult:
        """Send a prompt and return the complete response text with metadata."""
        parts: list[str] = []
        result_event: Result | None = None
        async for event in self.send(prompt):
            if isinstance(event, AssistantText):
                parts.append(event.text)
            elif isinstance(event, Result):
                result_event = event

        text = "".join(parts)
        if result_event:
            return AskResult(
                text=text,
                usage=result_event.usage,
                cost_usd=result_event.total_cost_usd,
                duration_ms=result_event.duration_ms,
                is_error=result_event.is_error,
                num_turns=result_event.num_turns,
                duration_api_ms=result_event.duration_api_ms,
                stop_reason=result_event.stop_reason,
                result=result_event.result,
                api_error_status=result_event.api_error_status,
                subtype=result_event.subtype,
            )
        return AskResult(text=text)

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
                    self._cwd = event.cwd
                    self._mcp_servers = list(event.mcp_servers)
                    self._permission_mode = event.permission_mode
                    log.info(
                        "session started: id=%s model=%s tools=%d",
                        self._session_id,
                        self._model_name,
                        len(self._tools),
                    )

                # Handle permission requests via sandbox
                if isinstance(event, PermissionRequest):
                    await self._handle_permission(event)

                # Handle MCP requests for registered tools
                if isinstance(event, McpRequest):
                    handled = await self._handle_mcp_request(event)
                    if not handled:
                        # Unknown method or unknown server -- pass through
                        pass
                    elif event.message.get("method") == "tools/list":
                        # tools/list is fully internal, don't yield
                        continue
                    # tools/call is yielded so consumers can track calls

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
                    self._turn_count += 1
                    if event.usage is not None:
                        self._total_tokens += event.usage.input_tokens + event.usage.output_tokens
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

    async def _handle_mcp_request(self, request: McpRequest) -> bool:
        """Handle an MCP JSON-RPC request. Returns True if handled."""
        tools = self._tools_by_server.get(request.server_name)
        if tools is None:
            return False

        message = request.message
        method = message.get("method", "")
        rpc_id = message.get("id")

        if method == "tools/list":
            response = {
                "jsonrpc": "2.0",
                "id": rpc_id,
                "result": {
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.input_schema,
                        }
                        for t in tools
                    ],
                },
            }
            msg = McpResponse(request_id=request.request_id, mcp_response=response)
            await write_message(self._process_mgr.stdin, msg)
            return True

        if method == "tools/call":
            params = message.get("params", {})
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})

            # Find the tool by name
            handler = None
            for t in tools:
                if t.name == tool_name:
                    handler = t.handler
                    break

            if handler is None:
                response = {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                }
            else:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = await handler(**arguments)
                    else:
                        result = handler(**arguments)
                    if isinstance(result, (dict, list)):
                        import json
                        result_text = json.dumps(result)
                    elif isinstance(result, str):
                        result_text = result
                    else:
                        result_text = str(result)
                    response = {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "result": {
                            "content": [{"type": "text", "text": result_text}],
                        },
                    }
                except Exception as e:
                    response = {
                        "jsonrpc": "2.0",
                        "id": rpc_id,
                        "error": {"code": -32000, "message": str(e)},
                    }

            msg = McpResponse(request_id=request.request_id, mcp_response=response)
            await write_message(self._process_mgr.stdin, msg)
            return True

        # Unknown JSON-RPC method
        return False

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
