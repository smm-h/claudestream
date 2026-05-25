# Heartbeat: scheduled agent invocation

## Idea

Give claudestream agents an optional "heartbeat" — periodic invocation on a schedule managed by the OS. The agent wakes up, does its work, exits. Stateless between runs (reads state from disk if needed).

## Why OS-level scheduling

OS schedulers (systemd timers, launchd, cron, Windows Task Scheduler) handle the hard parts: surviving reboots, catching up on missed runs, preventing overlapping executions, logging, resource limits. Reimplementing any of this in-process is worse in every dimension except portability — and portability is solvable with a backend abstraction.

## Backend matrix

Four backends, each with different capability profiles:

- **systemd timers** (Linux): richest feature set — missed-run recovery, concurrency control, journald logging, cgroup resource limits, sub-second accuracy. Requires `loginctl enable-linger` for user-level timers to fire without a login session. Environment is NOT inherited from shell — must be explicit.
- **launchd** (macOS): good — concurrency control built-in, catches up after sleep (but NOT after full power-off). Modern API is `launchctl bootstrap/bootout`, not `load/unload`. No `~` expansion in plist paths. TCC/Full Disk Access may be needed.
- **Windows Task Scheduler**: capable — missed-run recovery via `StartWhenAvailable`, concurrency control via `MultipleInstances`. Programmable from Python via `win32com` COM API or `schtasks.exe`.
- **cron** (POSIX fallback): universal but bare — no missed-run recovery, no concurrency control, no logging. Gaps can be shimmed: `mkdir`-based locking for concurrency (since `flock` isn't portable — missing on macOS, FreeBSD, Alpine), output redirection for logging. But missed-run recovery can't be shimmed.

## Abstraction shape

A single API surface that takes (schedule, command, identity) and delegates to the detected (or chosen) backend. The abstraction should expose backend capabilities so consumers know what guarantees they're getting — not pretend all backends are equal.

No existing Python library does this. Per-backend primitives exist (`python-crontab`, `pystemd`, `pywin32`) but they're thin, mostly unmaintained, and share no common interface. This would be built from scratch.

## Open questions

- Should this live in claudestream or be a standalone package? It's useful beyond claudestream — any Python project might want cross-platform scheduled tasks.
- How does the agent get its prompt/instructions on each heartbeat invocation? From a file? From the CLI command? From a config?
- Should the heartbeat feature integrate with session resumption (`--resume`) so the agent can optionally maintain context across invocations?
- How does the consumer inspect/manage installed heartbeats? `claudestream heartbeat list/remove/status`?
- Should the abstraction expose backend capabilities as flags (e.g., `supports_missed_run_recovery`, `supports_concurrency_control`) or silently shim what it can?
- Is Windows support worth the cost? The COM API is a different world from file-based backends.
- What's the minimum viable version? Maybe just systemd + launchd, with cron as a fallback?
