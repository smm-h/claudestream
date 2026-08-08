# Configurable MCP support

## Context

This library sits between a caller and the Claude Code CLI subprocess. It
touches MCP in exactly two places, and they point in opposite directions:

1. **External MCP servers — opaque passthrough.** `McpOptions(config_files,
   strict)` (`claudestream/_options.py:44-48`) reaches `SessionConfig.mcp`
   (`_options.py:131`) and `AgentDefinition.mcp` (`_agent.py:43`, merged at
   `_agent.py:320,388`), is copied into `ProcessConfig.mcp_config`
   (`_async_session.py:198-202,283,292`), and emitted as `--mcp-config` /
   `--strict-mcp-config` (`_process.py:124,133,184,194`). Nothing is validated;
   paths are never checked. It is public (`__init__.py:68,153`, README.md:174)
   but unreachable from this library's own CLI — no command registers an MCP
   flag, so only `.agent.json` files can carry it.
2. **SDK-defined tools — this library acts as an MCP server.** `@tool`
   (`_tools.py:188-237`) derives JSON Schema from type hints. Tools are grouped
   per server (`_async_session.py:92-95`), `mcp__<server>__*` is added to
   `--allowedTools` (`:175-177`), servers are announced via
   `InitializeRequest(sdk_mcp_servers=...)` then `McpSetServers`
   (`:446-464`), and a blocking handshake waits for each server's `tools/list`
   (`:506-545`). The responder `_handle_mcp_request()` (`:952-1071`) answers
   only `tools/list`, `initialize`, `notifications/initialized`, `tools/call`.
   Transport is not MCP stdio: requests arrive tunneled as `control_request` /
   `subtype: "mcp_message"` (`_protocol.py:269-277`) and replies go back as
   `control_response` (`messages.py:121-137`).

Claude Code spawns external MCP servers itself. Their traffic never crosses
this library's control channel — only `type: "sdk"` servers do.

## Problem

Three live defects, independent of any new feature:

- **Multiple `--mcp-config` files are broken.** `build_argv` comma-joins every
  list flag (`_process.py:224-225`), but the CLI documents `--mcp-config
  <configs...>` as space-separated. Two files become one argument
  `a.json,b.json`. Single-file use works; N>1 does not. The same joiner serves
  `--add-dir`, `--file`, and `--plugin-dir`, so the whole list-flag class needs
  a per-flag audit.
- **`initialize` ignores the client's proposed protocol version.**
  `_async_session.py:987` returns the literal `"2025-11-25"` regardless of what
  the client requested. No echo, no negotiation, no rejection. If the client
  ever proposes a different revision, this library answers with one the client
  did not offer — a latent spec violation.
- **`strict`'s docstring is wrong.** It says "Reject unknown MCP server names";
  the real flag means "only use servers from `--mcp-config`, ignore all other
  MCP configurations."

And the feature gap: external MCP configuration is an unvalidated list of file
paths, absent from this library's CLI, and therefore unreachable for any
downstream consumer that builds sessions through it.

## Solutions considered

| Scope | Pros | Cons | Effort |
|---|---|---|---|
| Correctness fixes (list-flag joiner audit, `initialize` negotiation, `strict` docstring, multi-block/structured tool results) | All small; the first two are live defects; multi-block results unlock images and resource links that text-only cannot express | None — these are repairs, not additions | Small |
| Typed external-server API: named structs (stdio command/args/env, http url/headers), validated at construction, serialized to `--mcp-config`; raw file paths kept as a separately-named explicit field; CLI flags added | Turns an unvalidated path list into a checked surface; fixes multi-config as a side effect; downstream consumers inherit it | Duplicates a schema the CLI owns, so drift is possible when it gains fields | Medium |
| Runtime reconfiguration probe: send `mcp_set_servers` with a `stdio` server mid-session against the real CLI and observe | Decides whether hot add/remove of external servers is possible without a process bounce; one experiment, no design commitment | None; it is a probe | Small |
| `resources/*` and `prompts/*` on the SDK server | Completes the server surface | No consumer today, and whether the CLI ever requests them is unverified — building first risks unused surface | Medium |
| This library becomes an MCP client itself, connecting to external servers and re-hosting their tools as SDK tools | The only design giving per-tool filtering, full traffic visibility, and revision independence from the CLI | Owning a real MCP client implementation across two spec revisions; duplicates what the CLI already does | Large |

## Decisions already made

- **No user-facing MCP version knob.** Who controls the revision differs per
  surface: for external servers it is the CLI and the external server talking
  to each other (this library is not in that conversation, so a knob would be
  dead config); for the tunneled SDK server the CLI proposes and this library
  answers, so the era is dictated by the CLI, not chosen by a user. The correct
  behavior is to respond to whichever method family arrives on the wire —
  `initialize` today, `server/discover` plus per-request `_meta` if and when the
  CLI moves to the 2026-07-28 revision — echoing a supported proposed version
  and hard-erroring on an unsupported one. This is in-protocol negotiation
  auto-detected from the wire, not a fallback: each request's method selects one
  code path unambiguously.
- **`resources/*` and `prompts/*` are excluded** until a probe shows the CLI
  actually requests them.

## Open decision

Whether this library becomes an MCP client (the last row above). It is the only
design where MCP traffic can be filtered, observed, or version-managed here at
all, and it implies a different public API than the typed-passthrough scope. It
should be decided explicitly rather than by omission.

## Affected files

- `claudestream/_options.py` — `McpOptions`, `SessionConfig.mcp`
- `claudestream/_process.py` — `build_argv` list-flag joining, `--mcp-config`
  and `--strict-mcp-config` emission
- `claudestream/_async_session.py` — `_handle_mcp_request()`, the `initialize`
  response, `mcp_set_servers` wiring, the handshake waiter
- `claudestream/_agent.py`, `claudestream/_agent_schema.py` — agent-file MCP
  options
- `claudestream/_cli.py` — new MCP flags
- `claudestream/_tools.py` — tool result shapes
- `tests/` — list-flag argv tests, negotiation tests, tool-result shape tests

## Effort

Small for the correctness fixes and the probe; medium for the typed API plus
CLI surface; large only if the MCP-client direction is chosen.
