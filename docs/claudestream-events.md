---
title: claudestream.events
description: "Typed event dataclasses for every Claude Code stream output event, including assistant messages, tool use, permissions, and results."
nav_group: "API Reference"
nav_order: 11
---

# claudestream.events

:-: ref path="claudestream.events"

## Type Aliases

**ContentBlock** is a union of all content block types: `TextBlock | ToolUseBlock | ThinkingBlock | ToolResultBlock`. Used as the element type of `AssistantMessage.content`.
