"""NDJSON protocol layer for Claude Code stream-json."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Union

from claudestream.events import (
    ApiRetry,
    AssistantMessage,
    AssistantText,
    CompactBoundary,
    ContentBlock,
    Event,
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
)
from claudestream.messages import (
    AllowPermission,
    DenyPermission,
    InitializeRequest,
    McpResponse,
    UserMessage,
)

log = logging.getLogger("claudestream")

# Type alias for all writable messages
Writable = Union[UserMessage, AllowPermission, DenyPermission, McpResponse, InitializeRequest]


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
        )
    # Unknown block type -- treat as text with empty content
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
    """Map a raw JSON dict to the correct typed Event dataclass."""
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

    # -- sdk control request (permission / mcp) ------------------------------
    if event_type == "sdk_control_request":
        request = raw.get("request", {})
        subtype = request.get("subtype", "")
        if subtype == "permission":
            return PermissionRequest(
                type="sdk_control_request",
                session_id=session_id,
                uuid=uuid,
                request_id=request.get("request_id", ""),
                tool_name=request.get("tool_name", ""),
                tool_input=request.get("tool_input", {}),
                decision_reason=request.get("decision_reason", ""),
                tool_use_id=request.get("tool_use_id", ""),
            )
        if subtype == "mcp_message":
            return McpRequest(
                type="sdk_control_request",
                session_id=session_id,
                uuid=uuid,
                request_id=request.get("request_id", ""),
                server_name=request.get("server_name", ""),
                message=request.get("message", {}),
            )
        return UnknownEvent(type=event_type, session_id=session_id, uuid=uuid, raw=raw)

    # -- rate limit ----------------------------------------------------------
    if event_type == "rate_limit":
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

    # -- unknown -------------------------------------------------------------
    return UnknownEvent(type=raw.get("type", "unknown"), session_id=session_id, uuid=uuid, raw=raw)


# ---------------------------------------------------------------------------
# Flattener
# ---------------------------------------------------------------------------


def flatten_event(event: Event) -> list[Event]:
    """Expand an event into convenience events (one per content block)."""
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
