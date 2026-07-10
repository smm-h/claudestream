# `Sandbox.bare` / `--bare` silently breaks CLI authentication

## Context

`Sandbox.bare=True` (and the equivalent `ProcessConfig.bare`) maps to the Claude
Code CLI's `--bare` flag. The documented intent is "Suppress CLAUDE.md loading"
(see the `Sandbox` field docs and `ProcessConfig.bare` comment).

## Problem

With Claude Code CLI **2.1.206**, `--bare` does more than suppress CLAUDE.md: it
suppresses OAuth credential loading. A session that authenticates via
`CLAUDE_CODE_OAUTH_TOKEN` (e.g. a claudewheel profile whose config dir has no
`.credentials.json`) works fine without `--bare`, but with `--bare` every call
returns a terminal Result of:

```json
{"type":"result","subtype":"success","is_error":true,"api_error_status":null,
 "result":"Not logged in · Please run /login","total_cost_usd":0,
 "usage":{...all zeros...}}
```

Note the contradiction the CLI itself emits: `subtype="success"` while
`is_error=true`. claudestream maps both faithfully (`raw.get("is_error", False)`,
`raw.get("subtype","")`), so `AskResult.is_error` is `True` and
`AskResult.subtype` is `"success"`. Consumers that gate on `is_error` correctly
reject the call as a failure, but the failure is opaque ("Not logged in" is only
in `result`/text), and the root cause (the `bare` flag) is non-obvious.

## Decisive evidence

Bisecting the summarizer argv against the raw CLI in a container with a valid
`sk-ant-oat01...` token:

| argv added to a working `claude -p ... --output-format json` | result |
| --- | --- |
| baseline | `is_error=False subtype=success result='OK'` |
| `--bare` | `is_error=True subtype=success result='Not logged in ...'` |
| `--no-session-persistence` | ok |
| `--permission-prompt-tool stdio` | ok |
| `--system-prompt test` | ok |

Only `--bare` triggers the failure.

## Options

1. **Document the footgun.** Update the `Sandbox.bare` / `ProcessConfig.bare`
   docs to warn that `--bare` suppresses credential loading in current CLI
   versions and must not be used with token-based (OAuth) auth. Lowest effort;
   keeps the flag available for callers who know what they are doing.
2. **Detect and hard-error.** When `bare=True` and auth is token-based
   (`CLAUDE_CODE_OAUTH_TOKEN` present, no on-disk credentials), raise at session
   start rather than letting every call fail opaquely with "Not logged in".
   Follows the project's "hard errors, not silent degradation" philosophy.
3. **Split the concern.** If a CLI flag exists that suppresses only CLAUDE.md
   without touching auth, map `bare` to that instead, so the documented intent
   (no CLAUDE.md) no longer carries the auth side effect. Needs CLI research;
   may not exist.

## Affected files

- `claudestream/_process.py` (`ProcessConfig.bare`, `build_argv`)
- `claudestream/policy.py` (`Sandbox.bare` field + docs)
- Docs/CLAUDE.md `Sandbox` table ("Suppress CLAUDE.md loading (passes --bare)")

## Effort

Option 1: ~15 min (docs only). Option 2: ~1-2 h (detection + red-green test
feeding a synthetic "Not logged in" success Result). Option 3: unknown (depends
on CLI capabilities).
