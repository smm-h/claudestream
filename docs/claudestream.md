---
title: claudestream
description: "A Python library and CLI for streaming Claude Code's JSON protocol, providing typed events, async/sync sessions, and tool registration."
nav_group: "API Reference"
nav_order: 1
---

# claudestream

:-: ref path="claudestream"

## Public API

Everything below is importable directly from `claudestream`. The symbols are grouped by category.

### Sessions

- **AsyncSession** -- async context manager for streaming Claude Code events
- **SyncSession** -- synchronous wrapper that bridges the async protocol to a blocking iterator
- **ClaudeStreamError** -- base exception for session errors

### Events

Typed dataclasses for every Claude Code stream output:

- **Event** -- base class for all stream events
- **SystemInit** -- first event in the stream, containing session metadata
- **ApiRetry** -- emitted before retrying a failed API call
- **CompactBoundary** -- emitted when conversation history is compacted
- **AssistantMessage** -- complete assistant response with content blocks
- **AssistantText** -- single text block extracted from an assistant message
- **ToolResultMessage** -- tool execution results returned to the model
- **ToolUse** -- single tool call extracted from an assistant message
- **ToolResult** -- single tool result
- **FileWrite** -- derived event when a Write tool succeeds
- **FileEdit** -- derived event when an Edit or MultiEdit tool succeeds
- **Thinking** -- single extended thinking block from an assistant message
- **StreamDelta** -- partial streaming token wrapping a raw API event
- **Result** -- final event in a turn with cost and usage summary
- **RateLimit** -- rate limit status change
- **PermissionRequest** -- permission request surfaced when the sandbox cannot auto-resolve
- **McpRequest** -- MCP tool call request from Claude Code
- **HookEvent** -- hook lifecycle event
- **UnknownEvent** -- forward-compatible event for unrecognized types
- **ControlResponse** -- response to a control request
- **AskResult** -- complete response from a single ask() call

### Content Blocks

- **TextBlock** -- text content block from an assistant message
- **ToolUseBlock** -- tool use content block from an assistant message
- **ThinkingBlock** -- extended thinking content block
- **ToolResultBlock** -- tool result content block
- **ContentBlock** -- union type alias for all content block types
- **Usage** -- token usage statistics for an API call

### Messages

Typed structs for Claude Code stream input:

- **AllowPermission** -- allow a permission request
- **DenyPermission** -- deny a permission request
- **InitializeRequest** -- SDK initialization request sent at session start
- **McpResponse** -- response to an MCP tool call request
- **McpSetServers** -- register SDK MCP servers with the Claude Code CLI
- **UserMessage** -- a user prompt sent to Claude Code via stdin

### Protocol

NDJSON protocol layer for reading and writing the stream:

- **Writable** -- union type alias for all writable message types
- **flatten_event** -- expand an event into convenience events (one per content block)
- **parse_event** -- map a raw JSON dict to the correct typed Event
- **read_events** -- async generator that reads NDJSON lines and yields parsed Events
- **write_message** -- serialize a message to NDJSON and write it to the stream

### Policy

Sandbox and permission policy types:

- **Allow** -- allow a tool to execute
- **Deny** -- deny a tool execution
- **Sandbox** -- declarative sandbox configuration controlling tools, filesystem scope, and flags
- **create_sandbox** -- create a validated Sandbox configuration
- **BUILTIN_TOOLS** -- frozenset of Claude Code's built-in tool names

### Tools

Tool registration API for user-defined MCP tools:

- **Tool** -- a user-defined tool struct served via MCP to Claude Code
- **collect_tools** -- gather all @tool-decorated functions from a module
- **tool** -- decorator factory that creates a Tool from a function's type hints and docstring

### Options

Configuration structs for session setup:

- **Budget** -- cost, turn, and token limits for a session
- **DebugOptions** -- debug output configuration
- **McpOptions** -- external MCP server configuration
- **PluginOptions** -- plugin loading configuration
- **ProcessLimits** -- process-level buffer and timeout tuning
- **SessionConfig** -- unified configuration object for all session types
- **SessionResolution** -- how to resolve which session to use
- **StreamOptions** -- stream output behavior controls
- **ToolSchema** -- tool schema without handler for JSON-serializable agent definitions

### Process

Subprocess management:

- **ProcessConfig** -- configuration for spawning a Claude Code subprocess
- **ProcessManager** -- manages the Claude Code subprocess lifecycle

### Agents

Agent definition and invocation:

- **AgentDefinition** -- a complete agent definition loadable from .agent.json files
- **discover_agents** -- discover agent definitions from filesystem and package resources
- **invoke_agent** -- async context manager that creates an AsyncSession from an AgentDefinition
- **invoke_agent_sync** -- sync context manager that creates a SyncSession from an AgentDefinition
- **load_agent** -- load an AgentDefinition from a .agent.json file or by bare name
- **resolve_prompt** -- resolve {variable} placeholders in a prompt template
