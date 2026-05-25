# Pydantic / msgspec event models

## Problem
Event models are plain frozen dataclasses with manual parsing in `_protocol.py`. This works but:
- No automatic JSON validation or error messages on schema mismatches
- Manual `.get()` extraction is verbose and error-prone
- No schema generation for documentation

## Options
1. **Pydantic v2** — automatic JSON parsing, good error messages, schema generation. Adds a dependency.
2. **msgspec** — fastest JSON decoding, minimal overhead, struct-based. Less well-known.
3. **Keep dataclasses** — zero deps, full control, but more manual work.

## Affected files
- `claudestream/events.py`
- `claudestream/_protocol.py` (parse_event, parse_content_block, parse_usage)
- `tests/test_events.py`

## Effort
Medium — mechanical migration of dataclasses to Pydantic/msgspec models, rewrite parse_event to use model_validate.
