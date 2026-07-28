# Audit the strictspec adoption (external session hand-off)

An external session added schema validation for at-rest `.agent.json`
documents via strictspec 0.1.0 (now a declared dependency). Committed but NOT
released — rides along with the next release. This todo exists so the work
can be audited first.

## What changed and why

Why: agent-definition files are agent-/human-edited documents with no prior
validation; strictspec (the fleet's validation authority) now gates them at
load with hard errors and a format_version gate.

- `.strictspec/agent-definition.schema.toml` (greenfield schema covering
  AgentDefinition/ToolSchema/Budget/Sandbox/McpOptions/StreamOptions;
  `input_schema` is an opaque leaf with declared stance; `version` remains
  the agent's own semantic version, distinct from the gate) + manifest +
  generated validator (committed, 444).
- `load_agent` and `discover_agents` validate raw bytes BEFORE msgspec
  decoding, via a shared helper; `AgentValidationError` exported.
- NET-NEW BREAKING: `.agent.json` files require integer `format_version = 1`;
  gate-absent yields a structured remediation message.
- Zero at-rest corpus existed, so the diff deploy-gate is discharged by a
  committed adjudication file (first real use of that mechanism) — verify you
  agree with its justification text.
- In-code construction paths are untouched (validation is at-rest only).
- Suite: 690 passed; 12 pre-existing live-API 403 failures are environmental.

## Audit points (known leftovers from the external audit)

1. The deprecated-`max_cost_usd` targeted remediation runs only in
   `load_agent`, not `discover_agents` — a legacy file found via discovery
   gets a generic unknown-key error instead of the targeted budget hint.
   Unify the two entry points.
