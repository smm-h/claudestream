# Integration tests: explicit opt-in + recorded-replay (VCR) lane

Provenance: the design below was adopted from a recommendation `[%%]` (freely reversible).

## Problem

`tests/test_integration.py` (19 tests) and `tests/test_mcp_handshake_integration.py` (1 test)
run the REAL claude binary against the real "personal" profile — live tokens, real API spend —
on every local full-suite run. The autouse skip guard (`tests/conftest.py:39-52`) checks only
prerequisites (binary on PATH + resolvable profile), so locally they always run: every
fix-verify loop burns quota implicitly. They cannot be mocked away — the real CLI wire
protocol IS what they test (the only detector of upstream protocol drift).

## Decided design `[%%]`

1. **Opt-in flip**: the `integration` marker additionally requires an explicit env flag (new
   convention, e.g. CLAUDESTREAM_INTEGRATION=1); excluded from default runs and from any
   sandboxed tier. The live lane becomes the deliberate drift detector — run manually or on
   CLI version bumps; re-record procedure documented.
2. **VCR replay lane** for default runs, at zero spend: a stand-in responder binary replays
   captured real transcripts through the FULL stack — real `create_subprocess_exec`, real
   stdin encoding, bidirectional control round-trips (exactly what the existing StreamReader
   doubles cannot exercise). Redirect via `SessionConfig.binary` (first-class field;
   `_process.py:33` `find_binary` — no monkeypatching). Transcripts live under a new
   `tests/fixtures/transcripts/` convention.
3. Protocol surface the responder must speak (from the current tests): NDJSON stream-json both
   directions; `control_request` subtypes `initialize` (hooks, sdk_mcp_servers),
   `mcp_set_servers`, `permission`; `control_response` (incl. nested `mcp_response` JSON-RPC);
   `mcp_message` wrapping JSON-RPC `initialize` / `notifications/initialized` / `tools/list` /
   `tools/call`; `result` events with cost/duration; realistic `SystemInit`, `StreamDelta`,
   `AssistantText`, file-write events. MCP protocolVersion currently `2025-11-25`.
4. Profile env comes from the upstream profile-resolution API, which is gaining an optional
   workspace-injection parameter (additive) — use it so replay tests never touch the real
   workspace.

Stale transcripts between re-records are fine: replay pins this library against a known
protocol version; the live lane is what notices the world moved.
