# A per-line envelope for the JSONL event stream

## Context

strictcli has grown a machine-output mode. `--json` is framework-reserved at
every level (declaring a flag by that name is a registration-time error), and
under it stdout carries **exactly one document**: an envelope with
`interface_version`, `app`, `app_version`, `command`, `exit_code`, `payload`,
`dry_run`, `preview`, `preview_error` and `diagnostics`. A command supplies
its machine value through `ctx.payload(value)`, declares that value's JSON
Schema at registration time (a closed subset: `type` including type lists,
`properties`, `required`, `items`, `enum`, `const`, `additionalProperties`),
and the framework validates the value where it writes the envelope.

Consumers across the fleet are being converted to it: a command that declared
its own `--json`, `--format json` or `--output json` loses that flag, declares
a payload schema, and its machine output becomes the envelope.

claudestream is deliberately **excluded** from that conversion, and this file
records why and what would have to be designed first.

## Problem

claudestream's machine output is not a document. It is a **stream**: one JSON
object per line, emitted as protocol events arrive from the subprocess, for
the whole lifetime of a session that can run for minutes.

Two surfaces produce it, both through `_print_json` in `claudestream/_cli.py`:

- `claudestream send --json-output` — serializes each protocol event as a JSON
  line instead of the human rendering.
- `claudestream events` — the debug mode, which is JSONL unconditionally (raw
  protocol events, with a human footer on stderr).

The single-document envelope cannot express this:

1. **The envelope is written once, at the end of dispatch.** A stream's whole
   value is that a line is readable the moment it is produced. Collecting every
   event into one `payload` would turn a live stream into a batch answer
   delivered after the session ends.
2. **`ctx.payload(...)` is at most once per dispatch.** A second call is a hard
   error by design — two payloads are two answers to a question with one slot.
   A stream is N answers by construction.
3. **A schema is declared per command, not per line.** The event stream is a
   union: assistant text deltas, tool calls, tool results, a terminal result
   carrying cost and duration. Each line is a different shape.
4. **The exit code is an envelope field.** In a stream, the last line is
   emitted before the process knows its exit status.

So a per-line envelope has to be designed. It does not exist in the framework
today, and inventing one privately in this repo would produce a second,
divergent framing that the framework would later contradict.

## What the design has to answer

- **Fields.** Which of the single-document envelope's members belong on every
  line (`interface_version`? `app`? `command`?) and which are per-line
  (`payload`, a sequence number, an event kind discriminator). Repeating the
  static fields on every line is bytes on the wire; omitting them makes a line
  unidentifiable out of context.
- **A header and/or trailer line.** A first line carrying the run's identity
  and a last line carrying the terminal status is the obvious shape, and it
  raises its own questions: what does a consumer do with a stream that ends
  without a trailer (a killed process, a broken pipe)?
- **Relationship to the single-document envelope.** Is the per-line envelope a
  different `interface_version` of the same contract, a sibling contract with
  its own version, or the same envelope with a `payload` that happens to be one
  event? A consumer must be able to tell which framing it is reading from the
  first line alone.
- **Schema declaration for a union of line shapes.** The closed subset has no
  `oneOf`. Either every line's payload validates against one permissive schema,
  or the framework grows a per-line-kind declaration, or line payloads are
  declared by a discriminator field plus a map of kind to schema.
- **The exit code and terminal errors.** Where a stream reports that the
  session failed, and how that composes with the process's exit status.
- **Ordering and backpressure.** Whether the framing promises anything about
  flushing, and what a consumer may assume about line order.

## Solution sketches

### A. Wait for the framework to design a streaming envelope

Leave both surfaces exactly as they are; adopt whatever the framework
eventually specifies.

- **Pro:** one framing across every producer; no divergence to migrate away
  from later; no work here until there is something to adopt.
- **Con:** the current JSONL output stays outside the machine-output regime
  indefinitely — no declared schema, no validation, no version stamp on the
  wire. A consumer parsing it today has nothing to generate against.

### B. Design the per-line envelope here, propose it upstream

Write the specification in this repo (fields, header/trailer, versioning,
per-kind schema declaration), implement it behind the existing surfaces, and
offer it as the framework's streaming contract.

- **Pro:** the design is grounded in a real stream with real consumers rather
  than in the abstract; this repo is the only fleet producer of an event
  stream, so it is the natural place for the first draft.
- **Con:** if the framework later specifies something different, this becomes
  a breaking change for every consumer twice over. Pre-stable policy permits
  it, but it is still two migrations.

### C. Version-stamp the lines now, defer the framing

A minimal step: add `format_version` (and nothing else) to every emitted line,
so a future framing change is detectable by a consumer rather than silent.

- **Pro:** cheap; makes the eventual migration diagnosable from the data.
- **Con:** it is a partial framing that will itself be superseded — a third
  shape between today's bare events and the eventual envelope.

### D. Split the surfaces

Treat `events` (a debug view of raw protocol traffic) and `send --json-output`
(a machine interface to a session's results) as different contracts: the debug
view stays a bare protocol dump forever, and only the machine interface gains
a framing.

- **Pro:** the debug view's whole point is fidelity to what the subprocess
  sent; wrapping it changes what it is for.
- **Con:** two output shapes to document and to keep honest, and the line
  between "debugging" and "consuming" is exactly where consumers wander.

## Affected files

- `claudestream/_cli.py` — `_print_json` (the single emission point), the
  `--json-output` flag on `send`, the `events` command, and the `_stream_events`
  human loop that the machine path parallels.
- `claudestream/events.py` — the event model whose shapes the per-line schemas
  would have to describe.
- `claudestream/_protocol.py` — where raw protocol events are decoded, and the
  source of the shapes `events` dumps verbatim.
- Tests covering both surfaces, plus the docs pages that describe the JSON
  line output.
- `pyproject.toml` — a strictcli floor bump, if the framing arrives as a
  framework feature.

## Effort estimate

- Design round (the questions above, answered and written down): **half a day**,
  and it is the part that cannot be skipped.
- Implementation once the framing is decided: **2-4 hours** — one emission
  point, two surfaces, plus schema declarations.
- Test and docs update: **2-3 hours**.

The design is the whole cost. Until it is done, the honest state is that this
repo's streaming output is outside the machine-output regime, on purpose.
