# Session resumption (--resume)

## Problem
Currently each AsyncSession/SyncSession starts a fresh conversation. Claude Code supports `--resume <session_id>` to continue a previous session.

## Solution
Add `resume_session_id: str | None` parameter to AsyncSession/SyncSession. When set, pass `--resume <id>` to the CLI. Session history is restored from Claude Code's session storage.

## Affected files
- `claudestream/_async_session.py`
- `claudestream/_sync_session.py`
- `claudestream/_process.py` (ProcessConfig.build_argv)

## Effort
Small — add the flag, pass it through, test with a real session.
