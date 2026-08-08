#!/usr/bin/env python3
"""Record the cassettes that back claudestream's replay (VCR) test lane.

Each scenario is driven through claudestream's own stack with
``SessionConfig.binary`` pointed at ``tests/fixtures/claude_vcr.py`` in record
mode, so the cassette holds the real CLI's bytes and claudestream's real
replies -- nothing is hand-authored.

Two lanes, because two different things cost two different amounts:

``--lane free``
    Scenarios that never reach the model. The CLI is launched against a
    throwaway ``CLAUDE_CONFIG_DIR`` with no credentials, so the whole
    control plane -- the ``initialize`` round-trip, ``mcp_set_servers``, and
    the MCP JSON-RPC handshake (``initialize`` / ``notifications/initialized``
    / ``tools/list``) -- is captured verbatim for **$0.00**. The model call at
    the end returns ``is_error`` with ``total_cost_usd: 0``.

``--lane paid``
    Scenarios that need a real answer from a real model: streaming deltas,
    assistant text, a non-zero-cost ``result``, a ``tools/call`` round-trip,
    file-write events. These BILL THE PROFILE YOU NAME. Run them deliberately,
    on the cheapest model, and only when re-recording is actually needed.

Usage::

    scripts/record_transcripts.py --lane free
    scripts/record_transcripts.py --lane paid --profile <name>
    scripts/record_transcripts.py --lane free --only mcp_handshake

Cassettes are written to ``tests/fixtures/transcripts/`` and committed. Stale
cassettes are fine between re-records: replay pins this library against a known
protocol version, and the live lane (``CLAUDESTREAM_INTEGRATION=1``) is what
notices the world moved.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from claudestream import AsyncSession, SessionConfig, Sandbox, tool  # noqa: E402
from tests.vcr import (  # noqa: E402
    TRANSCRIPTS_DIR,
    VCR_BINARY,
    WORKSPACE_ENV,
    fake_workspace,
    record_env,
)

MODEL = "haiku"


@tool("test_server")
def greet(name: str) -> str:
    """Greet someone by name.

    Args:
        name: Name to greet.
    """
    return f"Hello, {name}!"


async def _mcp_handshake(config_kwargs: dict) -> None:
    """Startup handshake with one SDK MCP server registered.

    Exercises: initialize control_request/response, mcp_set_servers, and the
    full MCP JSON-RPC handshake. No model call, hence free.
    """
    config = SessionConfig(tools=[greet._tool], **config_kwargs)
    async with AsyncSession(config):
        pass


async def _simple_send(config_kwargs: dict) -> None:
    """A single prompt with no tools: system init, assistant frames, result."""
    config = SessionConfig(**config_kwargs)
    async with AsyncSession(config) as session:
        async for _ in session.send("respond with exactly the word 'pong'"):
            pass


async def _streaming_send(config_kwargs: dict) -> None:
    """A prompt whose answer arrives as incremental stream deltas."""
    config = SessionConfig(sandbox=Sandbox(skip_permissions=True), **config_kwargs)
    async with AsyncSession(config) as session:
        async for _ in session.send("respond with exactly 'hello world'"):
            pass


async def _tool_call(config_kwargs: dict) -> None:
    """A prompt that makes the model call an SDK MCP tool (tools/call)."""
    config = SessionConfig(
        tools=[greet._tool], sandbox=Sandbox(skip_permissions=True), **config_kwargs
    )
    async with AsyncSession(config) as session:
        async for _ in session.send(
            "Use the greet tool to greet Alice. Just call the tool, nothing else."
        ):
            pass


@dataclass(frozen=True)
class Scenario:
    name: str
    needs_auth: bool
    run: Callable[[dict], object]
    description: str


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("mcp_handshake", False, _mcp_handshake, "control plane + MCP handshake"),
    Scenario("simple_send", False, _simple_send, "system init, assistant frames, result"),
    Scenario("streaming_send", True, _streaming_send, "incremental stream deltas"),
    Scenario("tool_call", True, _tool_call, "MCP tools/call round-trip"),
)


def _record(scenario: Scenario, profile: str, binary: str, workspace: Path) -> int:
    cassette = TRANSCRIPTS_DIR / f"{scenario.name}.jsonl"
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    config_kwargs = {
        "model": MODEL,
        "profile": profile,
        "binary": str(VCR_BINARY),
        "env": record_env(cassette, binary),
    }
    print(f"recording {scenario.name}: {scenario.description}", flush=True)
    try:
        asyncio.run(scenario.run(config_kwargs))
    except Exception as exc:  # noqa: BLE001 -- a partial cassette is still informative
        print(f"  scenario raised {type(exc).__name__}: {exc}", flush=True)
    if not cassette.exists():
        print(f"  FAILED: no cassette written to {cassette}", flush=True)
        return 1
    lines = cassette.read_text(encoding="utf-8").count("\n")
    print(f"  wrote {cassette.relative_to(REPO_ROOT)} ({lines} records)", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lane",
        required=True,
        choices=["free", "paid"],
        help="free: no credentials, no spend. paid: bills --profile.",
    )
    parser.add_argument(
        "--profile",
        help="claudewheel profile to bill (required for --lane paid)",
    )
    parser.add_argument(
        "--binary",
        default=shutil.which("claude"),
        help="path to the real claude binary (default: first on PATH)",
    )
    parser.add_argument(
        "--only",
        action="append",
        help="record only this scenario (repeatable)",
    )
    args = parser.parse_args()

    if not args.binary:
        parser.error("no claude binary found on PATH; pass --binary")
    if args.lane == "paid" and not args.profile:
        parser.error("--lane paid must name the profile it bills via --profile")
    if args.lane == "free" and args.profile:
        parser.error("--lane free records without credentials; drop --profile")

    wanted = [s for s in SCENARIOS if s.needs_auth == (args.lane == "paid")]
    if args.only:
        wanted = [s for s in wanted if s.name in args.only]
        if not wanted:
            parser.error(f"--only matched no {args.lane}-lane scenario")

    failures = 0
    if args.lane == "free":
        workspace = Path(tempfile.mkdtemp(prefix="claudestream-record-"))
        profile = fake_workspace(workspace)
        # Point claudewheel at the throwaway workspace so resolve_profile runs
        # for real yet yields no token -- that is what makes this lane free.
        os.environ[WORKSPACE_ENV] = str(workspace)
        print(f"free lane: credential-less workspace at {workspace}", flush=True)
    else:
        workspace = Path.home()
        profile = args.profile
        print(
            f"PAID lane: recording {len(wanted)} scenario(s) on profile "
            f"{profile!r} with model {MODEL} -- this spends real money.",
            flush=True,
        )

    for scenario in wanted:
        failures += _record(scenario, profile, args.binary, workspace)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
