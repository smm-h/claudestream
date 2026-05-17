# Custom tool registration

## Context

A downstream project (Dijkstra -- AI-driven game generation) needs agents to call custom tools during a claudestream session. Claude Code has built-in tools (Write, Read, Edit, Bash), but Dijkstra needs additional tools that the executor handles:

- `create_child(goal, tools, scope, config, context, mode)` -- delegate work to a sub-agent
- `tap_out(reason)` -- signal that the agent cannot complete its task

These aren't Claude Code tools -- they're handled by the host application (Dijkstra's executor). The executor needs to intercept these tool calls, handle them, and return tool_results to the session.

## Problem

Currently claudestream passes through Claude Code's tool_use events but has no mechanism for the host application to register custom tools that:
1. Are included in the tools list sent to Claude (so the model knows they exist)
2. Are intercepted by the host when called (not executed by Claude Code)
3. Return host-computed tool_results back to the session

## Requirements

- Host application registers custom tools with name + input_schema (JSON Schema)
- When the model calls a custom tool, claudestream emits a ToolUse event but does NOT let Claude Code execute it
- The host processes the tool call and sends a ToolResultMessage back
- Custom tools coexist with Claude Code's built-in tools in the same session
- Multiple custom tools can be registered per session

## Design notes

This likely requires passing custom tool definitions to the claude CLI via `--allowedTools` or a similar mechanism, plus intercepting the tool approval flow so custom tools are handled by the host rather than Claude Code.

Alternatively, if Claude Code's permission system can be configured to "ask" for certain tool names, the host can intercept PermissionRequest events for custom tools, execute them, and return results.

## Effort

Medium. Requires understanding how Claude Code handles tool definitions and the permission/approval flow.
