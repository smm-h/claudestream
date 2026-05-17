# Pre-execution tool interception for scope enforcement

## Context

A downstream project (Dijkstra) needs to enforce filesystem scope restrictions on agents. Each agent is only allowed to write to specific files. When Claude Code calls its built-in Write or Edit tool, the write must be validated BEFORE it hits the disk.

## Problem

Currently, when Claude Code decides to call Write, it executes the write directly. By the time claudestream emits the ToolUse event, the file has already been written. The host application cannot reject an out-of-scope write without reverting it after the fact.

Dijkstra's current solution is filesystem-level sandboxing (chroot or similar), but a claudestream-level solution would be cleaner.

## Requirements

- Ability to intercept Write/Edit tool calls BEFORE Claude Code executes them
- Host application can approve or deny based on the file path
- Denied tool calls return an error tool_result to the model ("permission denied: file outside scope")
- Approved tool calls proceed normally

## Design notes

This might be achievable through Claude Code's existing permission system. If Claude Code is launched with `--permission-prompt-tool stdio`, it sends PermissionRequest events for tool calls. The host can respond with approve/deny. claudestream already has a policy system for this.

If the existing permission flow supports this, the fix might just be documentation and a convenience API. If not, it may require changes to how Claude Code is launched.

## Effort

Small-medium. Likely leveraging existing permission infrastructure, not building new mechanisms.
