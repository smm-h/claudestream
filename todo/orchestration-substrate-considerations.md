# Orchestration-substrate considerations

## Context

claudestream is the typed programmatic driver for Claude Code sessions.
Claude Code itself ships orchestration and typing capabilities in-band: a
Workflow tool that executes deterministic JavaScript programs over agent
fan-outs with schema-forced structured outputs; a typed findings channel for
code review; skills that carry executable toolchains delivered to the agent.
Each has a claudestream-side counterpart where the program is owned,
versioned, and testable in this repository instead of living inside the
harness. The items below are independent considerations, no pressure; the
first is the foundation the others build on.

## 1. Structured-output session contract

Problem: sessions return prose; a program consuming a session's work has to
parse text. The interface deterministic orchestration needs is typed results.

Direction: a first-class option on a session (or on `ask()`) taking a JSON
Schema. claudestream serves a StructuredOutput tool over the existing MCP
serving path, instructs the session to answer through it, validates the call
against the schema (rejecting mismatches so the model retries), and returns
the validated object as the turn's result instead of text.

Affected: `claudestream/_tools.py`, `claudestream/_async_session.py` (MCP
handshake and turn loop), `claudestream/_options.py`, events.
Effort: medium.

## 2. Deterministic orchestration layer

Problem: multi-session work with structure — phases, fan-out counts, vote
thresholds, dedup, termination — currently requires every consumer to
hand-roll the control loop. Structure enforced by prose drifts; structure
enforced by a program does not.

Direction: an orchestration module where control flow is plain Python and
sessions are the worker pool: parallel session fan-out with a concurrency
cap, phase sequencing, result aggregation, and verification votes (N
independent sessions prompted to refute a claim; a refutation threshold
decides survival). Builds directly on the structured-output contract (item 1)
— typed results are what make aggregation and vote counting mechanical.

Scope note: this is a library layer, not a DSL. The consumer writes an
ordinary Python function; claudestream provides the session-pool, budget, and
collection primitives.

Effort: large; worth splitting into fan-out primitives first, verification
patterns second.

## 3. Project toolbelt auto-discovery

Problem: serving custom tools to a session currently requires the launching
program to construct them. Projects have no way to declare "sessions working
here get these tools."

Direction: a repo commits its agent-facing tool definitions (the existing
`@tool` decorator and/or `.agent.json` tool schemas, discovered from a
declared location in the repo). Sessions launched in that repo automatically
get them served over MCP. This is the honest version of a skill system: real
code, versioned in the repository it serves, reviewed like everything else,
no marketplace and no prompt payloads.

Affected: `claudestream/_agent.py` (discovery precedent exists for agent
definitions), `claudestream/_tools.py`, CLI (`send`/`repl`/`agent run`).
Effort: medium.

## 4. Typed findings for review and audit sessions

Problem: review/audit sessions report prose findings, which cannot be
aggregated, deduplicated, or compared across rounds.

Direction: a findings schema (file, line, summary, failure scenario,
severity, verdict) plus agent definitions that force review sessions to emit
records through the structured-output contract (item 1). Findings become data:
countable, diffable across review rounds, consumable by scripts. Whether any
release tooling later consumes such findings files is its own decision
elsewhere; this item is only the schema and the producing agents.

Effort: small-medium once item 1 exists.
