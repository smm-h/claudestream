# send_message() convenience method

## Problem

Every consumer of claudestream that needs a simple "send prompt, get text back" interaction writes the same boilerplate:

```python
text_parts = []
async for event in session.send(prompt):
    if hasattr(event, "text") and event.text:
        text_parts.append(event.text)
response = "".join(text_parts)
```

This pattern appears in at least three downstream projects. The streaming event model is powerful for real-time UIs, but most programmatic use cases just want the final text.

## Proposed solution

Add a convenience method to `AsyncSession` and `SyncSession`:

```python
# Async
response: str = await session.ask("What is 2+2?")

# Sync
response: str = session.ask("What is 2+2?")
```

`ask()` calls `send()` internally, collects all `AssistantText` events (or text from flattened events), and returns the concatenated string. It also stores the `Result` event on the session for cost inspection after the call.

This does not replace `send()` — streaming is still the primary API. `ask()` is sugar for the common case.

## Scope

Small. One method on each session class. No new dependencies.
