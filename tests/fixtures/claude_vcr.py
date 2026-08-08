#!/usr/bin/env python3
"""A stand-in for the ``claude`` binary that records and replays transcripts.

This is claudestream's VCR: one executable with two modes, selected by the
``CLAUDESTREAM_VCR_MODE`` environment variable.

``record``
    Spawn the real binary (``CLAUDESTREAM_VCR_REAL_BINARY``) with the same
    argv and proxy both pipes verbatim, appending every NDJSON line to the
    cassette as it flows. What the cassette contains is therefore exactly what
    the real CLI said and exactly what claudestream said back -- there is no
    hand-authoring step.

``replay``
    Serve the cassette. Lines the CLI produced are written to stdout in the
    recorded order; at each point where the host spoke, one line is read from
    stdin and its *shape* (event type, and control subtype where present) is
    checked against the recording. A mismatch or a read timeout is a hard
    error with a diagnostic on stderr -- never a silent divergence.

Replay is wired in through ``SessionConfig.binary``, so the exercised stack is
the real one: ``asyncio.create_subprocess_exec``, real pipe buffering, real
NDJSON encoding on stdin, and real bidirectional control round-trips. That is
what the in-process StreamReader doubles cannot reach.

Correlation needs no rewriting in either direction: claudestream's own request
ids are deterministic (``init_1``, ``mcp_set_1``, ``ctrl_N``) and the CLI's are
chosen by the CLI, so a cassette's ids are still the ids in play on replay.

The file is stdlib-only and executable, because it runs as a program, not as an
imported module.

Cassette format (NDJSON, one JSON object per line):

    {"kind": "meta", "version": "2.1.220", "recorded_at": "...", "argv": [...]}
    {"kind": "cli",  "payload": {...}}   # CLI -> host
    {"kind": "host", "payload": {...}}   # host -> CLI
"""

from __future__ import annotations

import json
import os
import select
import subprocess
import sys
import threading
from datetime import datetime, timezone

MODE_ENV = "CLAUDESTREAM_VCR_MODE"
CASSETTE_ENV = "CLAUDESTREAM_VCR_CASSETTE"
REAL_BINARY_ENV = "CLAUDESTREAM_VCR_REAL_BINARY"
READ_TIMEOUT_ENV = "CLAUDESTREAM_VCR_READ_TIMEOUT"

DEFAULT_READ_TIMEOUT = 15.0

#: Exit code for every VCR-level failure (missing cassette, unexpected input,
#: timeout). Distinct from the guard shim's 97 so the two are never confused.
VCR_EXIT_CODE = 96

VERSION_FLAGS = {"-v", "--version"}


#: Keys whose values are an inventory of the recording machine (the operator's
#: slash commands, subagents, model catalogue, output styles, memory paths)
#: rather than protocol structure. Cassettes are committed to a public
#: repository, so these are emptied at record time. The key and the *kind* of
#: its value survive -- what is dropped is content claudestream never reads.
#:
#: Emptying is deliberately type-agnostic. The predecessor of this set only
#: emptied ``list`` values, and ``memory_paths`` arrives as a ``dict``: it was
#: copied verbatim and a real machine path shipped in a public cassette.
#: Whatever type the CLI chooses for one of these keys, its content goes.
_EMPTIED_INVENTORY_KEYS = frozenset(
    {
        "agents",
        "available_output_styles",
        "commands",
        "memory_paths",
        "models",
        "plugins",
        "skills",
        "slash_commands",
    }
)

#: Built-in tool names that are public Claude Code surface. A ``tools``
#: inventory also lists whatever the recording machine has configured locally
#: (the operator's own tools, plugin tools, MCP tools), which is not
#: claudestream's to publish, so the list is filtered down to this allowlist.
#: ``Read`` is load-bearing for ``tests/test_replay.py``, which asserts a
#: recognisable tool survives the round-trip.
_PUBLIC_TOOLS = frozenset(
    {
        "AskUserQuestion",
        "Bash",
        "BashOutput",
        "Edit",
        "ExitPlanMode",
        "Glob",
        "Grep",
        "KillShell",
        "MultiEdit",
        "NotebookEdit",
        "Read",
        "Skill",
        "SlashCommand",
        "Task",
        "TodoWrite",
        "WebFetch",
        "WebSearch",
        "Write",
    }
)

#: Placeholder for the recording machine's working directory.
_SCRUBBED_CWD = "/workspace"

#: Placeholder for an emptied string value, so the key keeps a string type.
_SCRUBBED_TEXT = "<scrubbed>"


def _emptied(value):
    """Return *value* stripped of content, keeping the type it arrived as.

    ``dict`` -> ``{}``, ``list`` -> ``[]``, ``str`` -> a placeholder string.
    Anything else (a number, a bool, ``None``) carries no machine-local text
    and is passed through.
    """
    if isinstance(value, dict):
        return {}
    if isinstance(value, list):
        return []
    if isinstance(value, str):
        return _SCRUBBED_TEXT
    return value


def _public_tools_only(items):
    """Drop non-public tool names from a ``tools`` inventory.

    Only *string* entries are filtered. The same key name carries MCP tool
    *objects* in a ``tools/list`` response -- those are claudestream's own
    tools, declared by the test that recorded the cassette, and they stay.
    """
    return [
        _scrub(item)
        for item in items
        if not isinstance(item, str) or item in _PUBLIC_TOOLS
    ]


def _scrub(value):
    """Strip machine-local content from a payload, preserving its shape."""
    if isinstance(value, dict):
        scrubbed = {}
        for key, item in value.items():
            if key in _EMPTIED_INVENTORY_KEYS:
                scrubbed[key] = _emptied(item)
            elif key == "tools" and isinstance(item, list):
                scrubbed[key] = _public_tools_only(item)
            elif key == "cwd" and isinstance(item, str):
                scrubbed[key] = _SCRUBBED_CWD
            elif key == "pid" and isinstance(item, int):
                scrubbed[key] = 0
            else:
                scrubbed[key] = _scrub(item)
        return scrubbed
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _die(message: str) -> int:
    sys.stderr.write(f"claude_vcr: {message}\n")
    sys.stderr.flush()
    return VCR_EXIT_CODE


def _signature(payload: dict) -> str:
    """Shape key for a host->CLI line: the part replay is allowed to depend on.

    Ids, session ids and prompt text vary between a recording and a replay of
    the same scenario; the event type and the control subtype do not.
    """
    kind = payload.get("type", "")
    if kind == "control_request":
        return f"control_request:{payload.get('request', {}).get('subtype', '')}"
    if kind == "control_response":
        return f"control_response:{payload.get('response', {}).get('subtype', '')}"
    return kind


class _LineReader:
    """Timeout-capable NDJSON line reader over a blocking pipe."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._buffer = b""
        self._eof = False

    def readline(self, timeout: float) -> bytes | None:
        """Return one line, or None on EOF/timeout."""
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line, self._buffer = self._buffer[: newline + 1], self._buffer[newline + 1 :]
                return line
            if self._eof:
                return None
            ready, _, _ = select.select([self._stream], [], [], timeout)
            if not ready:
                return None
            chunk = os.read(self._stream.fileno(), 65536)
            if not chunk:
                self._eof = True
                continue
            self._buffer += chunk

    def wait_for_eof(self) -> None:
        """Consume and discard input until the host closes stdin."""
        while not self._eof:
            chunk = os.read(self._stream.fileno(), 65536)
            if not chunk:
                self._eof = True


def _load_cassette(path: str) -> tuple[dict, list[dict]]:
    with open(path, encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle if line.strip()]
    if not records or records[0].get("kind") != "meta":
        raise ValueError(f"{path}: first line must be a meta record")
    return records[0], records[1:]


def _replay(cassette: str, argv: list[str]) -> int:
    try:
        meta, records = _load_cassette(cassette)
    except (OSError, ValueError) as exc:
        return _die(f"cannot load cassette: {exc}")

    if any(arg in VERSION_FLAGS for arg in argv):
        sys.stdout.write(f"{meta.get('version', '0.0.0')} (claude_vcr replay)\n")
        sys.stdout.flush()
        return 0

    timeout = float(os.environ.get(READ_TIMEOUT_ENV, DEFAULT_READ_TIMEOUT))
    reader = _LineReader(sys.stdin.buffer)

    for index, record in enumerate(records):
        if record["kind"] == "cli":
            sys.stdout.write(json.dumps(record["payload"], separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue

        line = reader.readline(timeout)
        if line is None:
            expected = _signature(record["payload"])
            return _die(
                f"{cassette}: record {index} expected the host to send "
                f"{expected!r}, but stdin gave EOF or nothing within {timeout}s"
            )
        try:
            actual_payload = json.loads(line)
        except json.JSONDecodeError as exc:
            return _die(f"{cassette}: record {index}: host sent non-JSON ({exc})")
        expected = _signature(record["payload"])
        actual = _signature(actual_payload)
        if expected != actual:
            return _die(
                f"{cassette}: record {index}: host sent {actual!r}, "
                f"cassette expects {expected!r}"
            )

    reader.wait_for_eof()
    return 0


def _record(cassette: str, argv: list[str]) -> int:
    real_binary = os.environ.get(REAL_BINARY_ENV)
    if not real_binary:
        return _die(f"{REAL_BINARY_ENV} must point at the real claude binary")

    if any(arg in VERSION_FLAGS for arg in argv):
        os.execv(real_binary, [real_binary, *argv])

    version = "unknown"
    try:
        probe = subprocess.run(
            [real_binary, "-v"], capture_output=True, text=True, timeout=15
        )
        version = probe.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        pass

    handle = open(cassette, "w", encoding="utf-8")
    lock = threading.Lock()

    def log(kind: str, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return
        with lock:
            handle.write(json.dumps({"kind": kind, "payload": _scrub(payload)}) + "\n")
            handle.flush()

    with lock:
        handle.write(
            json.dumps(
                {
                    "kind": "meta",
                    "version": version,
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                    "argv": argv,
                    "scrubbed": True,
                }
            )
            + "\n"
        )
        handle.flush()

    proc = subprocess.Popen(
        [real_binary, *argv],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )

    def pump_stdin() -> None:
        try:
            for line in iter(sys.stdin.buffer.readline, b""):
                log("host", line)
                proc.stdin.write(line)
                proc.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

    def pump_stdout() -> None:
        try:
            for line in iter(proc.stdout.readline, b""):
                log("cli", line)
                sys.stdout.buffer.write(line)
                sys.stdout.buffer.flush()
        except (BrokenPipeError, ValueError):
            pass

    stdin_thread = threading.Thread(target=pump_stdin, daemon=True)
    stdout_thread = threading.Thread(target=pump_stdout, daemon=True)
    stdin_thread.start()
    stdout_thread.start()

    code = proc.wait()
    stdout_thread.join(timeout=5)
    handle.close()
    return code


def main(argv: list[str]) -> int:
    mode = os.environ.get(MODE_ENV)
    cassette = os.environ.get(CASSETTE_ENV)
    if not cassette:
        return _die(f"{CASSETTE_ENV} must point at a cassette file")
    if mode == "replay":
        return _replay(cassette, argv)
    if mode == "record":
        return _record(cassette, argv)
    return _die(f"{MODE_ENV} must be 'record' or 'replay' (got {mode!r})")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
