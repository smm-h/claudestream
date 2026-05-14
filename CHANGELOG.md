# Changelog

## 0.1.0

- Async and sync session APIs (`AsyncSession`, `SyncSession`) for multi-turn conversations with Claude Code
- One-shot `print_prompt()` convenience function for simple use cases
- Typed event models for all Claude Code stream-json event types
- Real-time streaming token support via `StreamDelta` events
- Configurable permission policy system (allow_all, deny_all, allow_builtins, allow_list, custom callbacks)
- CLI with 4 commands: `send`, `stream`, `events`, `repl`
- Subprocess lifecycle management with graceful shutdown
