"""NDJSON protocol layer that reads raw Claude Code stream-json output lines and decodes them into typed Event objects for consumption."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Union

from claudestream.events import (
    ApiRetry,
    AssistantMessage,
    AssistantText,
    CompactBoundary,
    ContentBlock,
    ControlResponse,
    Event,
    FileEdit,
    FileWrite,
    HookEvent,
    McpRequest,
    PermissionRequest,
    RateLimit,
    Result,
    StreamDelta,
    SystemInit,
    TextBlock,
    Thinking,
    ThinkingBlock,
    ToolResult,
    ToolResultBlock,
    ToolResultMessage,
    ToolUse,
    ToolUseBlock,
    UnknownEvent,
    Usage,
    UserDialogRequest,
)
from claudestream.messages import (
    AllowPermission,
    ControlRequest,
    DenyPermission,
    DialogCancelled,
    DialogCompleted,
    InitializeRequest,
    McpResponse,
    McpSetServers,
    UserMessage,
)

log = logging.getLogger("claudestream")

# Type alias for all writable messages
Writable = Union[
    UserMessage,
    AllowPermission,
    DenyPermission,
    DialogCompleted,
    DialogCancelled,
    McpResponse,
    McpSetServers,
    InitializeRequest,
    ControlRequest,
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_content_block(raw: dict) -> ContentBlock:
    """Parse a single content block dict into the correct typed block."""
    block_type = raw.get("type", "")
    if block_type == "text":
        return TextBlock(text=raw.get("text", ""))
    if block_type == "tool_use":
        return ToolUseBlock(
            id=raw.get("id", ""),
            name=raw.get("name", ""),
            input=raw.get("input", {}),
        )
    if block_type == "thinking":
        return ThinkingBlock(thinking=raw.get("thinking", ""))
    if block_type == "tool_result":
        return ToolResultBlock(
            tool_use_id=raw.get("tool_use_id", ""),
            content=raw.get("content", ""),
            is_error=raw.get("is_error", False),
        )
    # Unknown block type -- treat as text with empty content
    log.warning("Unknown content block type: %s", raw.get("type"))
    return TextBlock(text=raw.get("text", ""))


def parse_usage(raw: dict | None) -> Usage | None:
    """Parse a usage dict into Usage, or return None."""
    if not raw:
        return None
    return Usage(
        input_tokens=raw.get("input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        cache_creation_input_tokens=raw.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=raw.get("cache_read_input_tokens", 0),
    )


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_event(raw: dict) -> Event:
    """Map a raw JSON dict to the correct typed Event Struct."""
    event_type = raw.get("type", "")
    session_id = raw.get("session_id")
    uuid = raw.get("uuid")

    # -- system events -------------------------------------------------------
    if event_type == "system":
        subtype = raw.get("subtype", "")
        if subtype == "init":
            return SystemInit(
                type="system",
                session_id=session_id,
                uuid=uuid,
                cwd=raw.get("cwd", ""),
                tools=raw.get("tools", []),
                mcp_servers=raw.get("mcp_servers", []),
                model=raw.get("model", ""),
                permission_mode=raw.get("permission_mode", ""),
                claude_code_version=raw.get("claude_code_version", ""),
            )
        if subtype == "api_retry":
            return ApiRetry(
                type="system",
                session_id=session_id,
                uuid=uuid,
                attempt=raw.get("attempt", 0),
                max_retries=raw.get("max_retries", 0),
                retry_delay_ms=raw.get("retry_delay_ms", 0.0),
                error_status=raw.get("error_status"),
                error=raw.get("error", ""),
            )
        if subtype == "compact_boundary":
            return CompactBoundary(
                type="system",
                session_id=session_id,
                uuid=uuid,
            )
        if subtype == "rate_limit":
            return RateLimit(
                type="rate_limit",
                session_id=session_id,
                uuid=uuid,
                status=raw.get("status", ""),
                resets_at=raw.get("resets_at") or raw.get("resetsAt"),
                rate_limit_type=raw.get("rate_limit_type") or raw.get("rateLimitType", ""),
                utilization=raw.get("utilization", 0.0),
            )
        # Unhandled system subtype -- log at DEBUG (not WARNING) since
        # new subtypes are expected as the CLI evolves.
        log.debug("system event with unhandled subtype %r", subtype)
        return UnknownEvent(type=event_type, session_id=session_id, uuid=uuid, raw=raw)

    # -- assistant message ---------------------------------------------------
    if event_type == "assistant":
        message = raw.get("message", {})
        raw_content = message.get("content", [])
        blocks = [parse_content_block(b) for b in raw_content]
        usage = parse_usage(message.get("usage"))
        return AssistantMessage(
            type="assistant",
            session_id=session_id,
            uuid=uuid,
            content=blocks,
            model=message.get("model", ""),
            stop_reason=message.get("stop_reason", ""),
            usage=usage,
            parent_tool_use_id=raw.get("parent_tool_use_id"),
            error=message.get("error"),
        )

    # -- user (tool result) message ------------------------------------------
    if event_type == "user":
        message = raw.get("message", {})
        raw_content = message.get("content", [])
        blocks = [
            parse_content_block(b)
            for b in raw_content
            if b.get("type") == "tool_result"
        ]
        return ToolResultMessage(
            type="user",
            session_id=session_id,
            uuid=uuid,
            content=blocks,
            parent_tool_use_id=raw.get("parent_tool_use_id"),
        )

    # -- result --------------------------------------------------------------
    if event_type == "result":
        usage = parse_usage(raw.get("usage"))
        return Result(
            type="result",
            session_id=session_id,
            uuid=uuid,
            subtype=raw.get("subtype", ""),
            is_error=raw.get("is_error", False),
            duration_ms=raw.get("duration_ms", 0.0),
            duration_api_ms=raw.get("duration_api_ms", 0.0),
            num_turns=raw.get("num_turns", 0),
            result=raw.get("result", ""),
            stop_reason=raw.get("stop_reason", ""),
            total_cost_usd=raw.get("total_cost_usd", 0.0),
            usage=usage,
            api_error_status=raw.get("api_error_status"),
            model_usage=raw.get("modelUsage", {}) or {},
        )

    # -- stream event --------------------------------------------------------
    if event_type == "stream_event":
        return StreamDelta(
            type="stream_event",
            session_id=session_id,
            uuid=uuid,
            event=raw.get("event", {}),
            parent_tool_use_id=raw.get("parent_tool_use_id"),
        )

    # -- control request (permission / mcp) ------------------------------------
    if event_type == "control_request":
        request = raw.get("request", {})
        subtype = request.get("subtype", "")
        request_id = raw.get("request_id") or request.get("request_id", "")
        if subtype in ("permission", "can_use_tool"):
            # The live CLI (subtype "can_use_tool") carries the tool input under
            # "input"; the older "permission" form used "tool_input". Read whichever
            # the wire supplies. The enriched fields below are optional in both.
            input_key = "input" if subtype == "can_use_tool" else "tool_input"
            return PermissionRequest(
                type="control_request",
                session_id=session_id,
                uuid=uuid,
                request_id=request_id,
                tool_name=request.get("tool_name", ""),
                tool_input=request.get(input_key, {}),
                decision_reason=request.get("decision_reason", ""),
                tool_use_id=request.get("tool_use_id", ""),
                permission_suggestions=request.get("permission_suggestions", []),
                title=request.get("title", ""),
                display_name=request.get("display_name", ""),
                description=request.get("description", ""),
                decision_reason_type=request.get("decision_reason_type", ""),
                requires_user_interaction=request.get("requires_user_interaction", False),
            )
        if subtype == "request_user_dialog":
            return UserDialogRequest(
                type="control_request",
                session_id=session_id,
                uuid=uuid,
                request_id=request_id,
                dialog_kind=request.get("dialog_kind", ""),
                payload=request.get("payload", {}),
                tool_use_id=request.get("tool_use_id"),
            )
        if subtype == "mcp_message":
            return McpRequest(
                type="control_request",
                session_id=session_id,
                uuid=uuid,
                request_id=request_id,
                server_name=request.get("server_name", ""),
                message=request.get("message", {}),
            )
        # Catch-all for hook lifecycle events and other control_request subtypes
        return HookEvent(
            type="control_request",
            session_id=session_id,
            uuid=uuid,
            hook_name=subtype,
            hook_data=request,
        )

    # -- rate limit ----------------------------------------------------------
    if event_type in ("rate_limit", "rate_limit_event"):
        info = raw.get("rate_limit_info", {})
        return RateLimit(
            type="rate_limit",
            session_id=session_id,
            uuid=uuid,
            status=info.get("status", ""),
            resets_at=info.get("resets_at"),
            rate_limit_type=info.get("rate_limit_type", ""),
            utilization=info.get("utilization", 0.0),
        )

    # -- control response ------------------------------------------------------
    if event_type == "control_response":
        resp = raw.get("response", {})
        subtype = resp.get("subtype", "")
        request_id = resp.get("request_id", "")
        response_data = resp.get("response", {})
        error_text = resp.get("error", "")
        return ControlResponse(
            type="control_response",
            session_id=session_id,
            uuid=uuid,
            request_id=request_id,
            subtype=subtype,
            response=response_data,
            error=error_text,
        )

    # -- unknown -------------------------------------------------------------
    return UnknownEvent(type=raw.get("type", "unknown"), session_id=session_id, uuid=uuid, raw=raw)


# ---------------------------------------------------------------------------
# Flattener
# ---------------------------------------------------------------------------


def _resolve_path(path: str, cwd: str | None) -> str:
    """Resolve a file path to absolute, using cwd if the path is relative."""
    if not path:
        return path
    if os.path.isabs(path):
        return os.path.normpath(path)
    if cwd:
        return os.path.normpath(os.path.join(cwd, path))
    return path


def _derive_file_events(block: ToolUseBlock, event: AssistantMessage, cwd: str | None) -> list[Event]:
    """Derive FileWrite/FileEdit events from a file-modifying ToolUseBlock."""
    derived: list[Event] = []
    inp = block.input

    if block.name == "Write":
        path = _resolve_path(inp.get("file_path", ""), cwd)
        derived.append(
            FileWrite(
                type="file_write",
                session_id=event.session_id,
                uuid=event.uuid,
                path=path,
                content_length=len(inp.get("content", "")),
            )
        )
    elif block.name == "Edit":
        path = _resolve_path(inp.get("file_path", ""), cwd)
        derived.append(
            FileEdit(
                type="file_edit",
                session_id=event.session_id,
                uuid=event.uuid,
                path=path,
            )
        )
    elif block.name == "MultiEdit":
        # MultiEdit may have a top-level file_path and/or per-edit file_path entries
        seen_paths: set[str] = set()
        edits = inp.get("edits", [])
        for edit in edits:
            edit_path = edit.get("file_path", "") or inp.get("file_path", "")
            resolved = _resolve_path(edit_path, cwd)
            if resolved and resolved not in seen_paths:
                seen_paths.add(resolved)
                derived.append(
                    FileEdit(
                        type="file_edit",
                        session_id=event.session_id,
                        uuid=event.uuid,
                        path=resolved,
                    )
                )
        # If no edits array but there's a top-level file_path, emit one event
        if not edits and inp.get("file_path"):
            path = _resolve_path(inp.get("file_path", ""), cwd)
            if path:
                derived.append(
                    FileEdit(
                        type="file_edit",
                        session_id=event.session_id,
                        uuid=event.uuid,
                        path=path,
                    )
                )

    return derived


def flatten_event(event: Event, cwd: str | None = None) -> list[Event]:
    """Expand an event into convenience events (one per content block).

    Args:
        event: The event to flatten.
        cwd: Working directory for resolving relative paths in file-tracking events.
    """
    if isinstance(event, AssistantMessage):
        results: list[Event] = []
        for block in event.content:
            if isinstance(block, TextBlock):
                results.append(
                    AssistantText(
                        type="assistant_text",
                        session_id=event.session_id,
                        uuid=event.uuid,
                        text=block.text,
                        parent_tool_use_id=event.parent_tool_use_id,
                    )
                )
            elif isinstance(block, ToolUseBlock):
                results.append(
                    ToolUse(
                        type="tool_use",
                        session_id=event.session_id,
                        uuid=event.uuid,
                        tool_use_id=block.id,
                        name=block.name,
                        input=block.input,
                        parent_tool_use_id=event.parent_tool_use_id,
                    )
                )
                # Derive file-tracking events after the ToolUse event
                results.extend(_derive_file_events(block, event, cwd))
            elif isinstance(block, ThinkingBlock):
                results.append(
                    Thinking(
                        type="thinking",
                        session_id=event.session_id,
                        uuid=event.uuid,
                        text=block.thinking,
                        parent_tool_use_id=event.parent_tool_use_id,
                    )
                )
        return results

    if isinstance(event, ToolResultMessage):
        results = []
        for block in event.content:
            results.append(
                ToolResult(
                    type="tool_result",
                    session_id=event.session_id,
                    uuid=event.uuid,
                    tool_use_id=block.tool_use_id,
                    content=block.content,
                    parent_tool_use_id=event.parent_tool_use_id,
                    is_error=block.is_error,
                )
            )
        return results

    return [event]


# ---------------------------------------------------------------------------
# NDJSON I/O
# ---------------------------------------------------------------------------


async def read_events(stream: asyncio.StreamReader) -> AsyncIterator[Event]:
    """Async generator that reads NDJSON lines and yields parsed Events."""
    while True:
        line = await stream.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            log.warning("skipping non-JSON line: %s", text[:200])
            continue
        yield parse_event(raw)


async def write_message(stream: asyncio.StreamWriter, msg: Writable) -> None:
    """Serialize a message to NDJSON and write it to the stream."""
    log.info("protocol: Sending %s", type(msg).__name__)
    data = json.dumps(msg.to_dict()) + "\n"
    stream.write(data.encode("utf-8"))
    await stream.drain()
