#!/usr/bin/env python3
"""Empirical wire probe for the Claude Code stream-json protocol.

Discovers how the `claude` CLI (driven with --input-format stream-json
--output-format stream-json) surfaces (a) the AskUserQuestion tool via the
`request_user_dialog` control_request and (b) interactive permission prompts.

This is a standalone raw-asyncio-subprocess probe. It deliberately does NOT go
through claudestream's AsyncSession so we see the unfiltered wire bytes. It
mirrors claudestream's argv construction (see claudestream/_process.py
build_argv) and its env handling (resolve_profile("personal") merged over
os.environ), exactly as claudestream's integration tests do.

Usage:
    probe_user_dialogs.py --scenario {A,B,C_completed,C_cancelled,D} --model MODEL

Captures are written to the scratchpad captures dir as one NDJSON file per
scenario. Every stdout line is recorded verbatim.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from claudewheel.profile import resolve_profile

CAPTURES_DIR = Path(
    "/tmp/claude-1000/-home-m-Projects/37ab5c49-a710-43ca-b5a2-6bf9f4da4f27/scratchpad/dialog-probe-captures"
)

COLOR_PROMPT = (
    "Use the AskUserQuestion tool to ask me which of two colors I prefer, "
    "red or blue, with one option per color. You MUST call the AskUserQuestion tool."
)
# Steering appended for B/C scenarios: this environment's profile injects the
# deferred-tool/ToolSearch machinery, which makes models try to "load"
# AskUserQuestion via ToolSearch (and fail) instead of emitting it directly.
# AskUserQuestion is a built-in dialog tool, never a deferred tool. This nudge
# only affects WHETHER the model emits the call; the CLI's execution-gate wire
# behavior we are probing is independent of it.
COLOR_PROMPT_DIRECT = (
    COLOR_PROMPT
    + " IMPORTANT: AskUserQuestion is a built-in tool you can invoke DIRECTLY. "
    "Do NOT use ToolSearch — call AskUserQuestion directly right now with a single "
    "question 'Which color do you prefer?' and two options labeled Red and Blue."
)
# 'whoami' is benign and NOT in the personal profile's allow/deny/ask lists, so
# default permission mode prompts for it (echo:* is allowlisted, so it can't
# trigger a prompt). This forces the permission control_request we want to probe.
BASH_PROMPT = "Run the bash command: whoami"

SUPPORTED_DIALOG_KINDS = [
    "AskUserQuestion",
    "ask_user_question",
    "user_question",
    "question",
    "multiple_choice",
    "choice",
    "refusal_fallback_prompt",
]

HARD_TIMEOUT = 175.0  # seconds, leave margin under the 180s external kill


def build_argv(
    binary: str,
    model: str,
    permission_prompt_tool: str | None,
    allowed_tools: list[str] | None = None,
) -> list[str]:
    """Mirror claudestream._process.ProcessConfig.build_argv for our needs."""
    argv = [
        binary,
        "--output-format",
        "stream-json",
        "--input-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
    ]
    if allowed_tools:
        argv += ["--allowedTools", ",".join(allowed_tools)]
    if permission_prompt_tool:
        argv += ["--permission-prompt-tool", permission_prompt_tool]
    return argv


def build_env() -> dict[str, str]:
    """resolve_profile('personal') merged OVER os.environ (claudestream test pattern)."""
    env = dict(os.environ)
    env.update(resolve_profile("personal"))
    return env


def initialize_frame() -> dict:
    """Mirror claudestream.messages.InitializeRequest envelope, plus supportedDialogKinds.

    claudestream places request_id INSIDE `request` and it works today, so we
    keep that proven shape and add the camelCase supportedDialogKinds key that
    the SDK typings declare (sibling of hooks).
    """
    return {
        "type": "control_request",
        # request_id at top level (SDKControlRequest) AND nested (claudestream's
        # proven shape) to cover both parser expectations.
        "request_id": "init_1",
        "request": {
            "subtype": "initialize",
            "request_id": "init_1",
            "hooks": {},
            "sdk_mcp_servers": [],
            # Send BOTH spellings: wire is snake_case, TS SDK is camelCase.
            "supported_dialog_kinds": SUPPORTED_DIALOG_KINDS,
            "supportedDialogKinds": SUPPORTED_DIALOG_KINDS,
        },
    }


def user_frame(prompt: str, session_id: str = "") -> dict:
    return {
        "type": "user",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
        "session_id": session_id,
    }


def build_completed_result(payload: dict) -> object:
    """Best-guess inner `result` for a completed AskUserQuestion dialog.

    Adapted after observing scenario B's actual payload. The payload's exact
    schema is what this probe discovers; this builder inspects the observed
    fields and constructs a plausible answer. Refine after seeing B.
    """
    # The AskUserQuestion payload (per real CLI) carries a top-level "questions"
    # array; each question has a "header" and an "options" list of {label, ...}.
    # The tool result the CLI expects mirrors the ExitPlanMode/AskUserQuestion
    # tool_result contract. We try the shape the CLI's own thin-client uses.
    questions = payload.get("questions") or payload.get("question") or []
    if isinstance(questions, dict):
        questions = [questions]
    answers = []
    for q in questions:
        opts = q.get("options") or []
        chosen = None
        for o in opts:
            label = o.get("label") if isinstance(o, dict) else o
            if isinstance(label, str) and "blue" in label.lower():
                chosen = o
                break
        if chosen is None and opts:
            chosen = opts[0]
        header = q.get("header") if isinstance(q, dict) else None
        chosen_label = chosen.get("label") if isinstance(chosen, dict) else chosen
        answers.append({"header": header, "label": chosen_label, "option": chosen})
    return {"answers": answers, "questions": questions}


class Probe:
    def __init__(self, scenario: str, model: str, answer_key: str = "question"):
        self.scenario = scenario
        self.model = model
        # answer_key selects how the E_answer scenario keys/shapes the answers it
        # injects into updatedInput on the can_use_tool gate:
        #   "question" -> answers is a map {question_text: chosen_label}
        #   "header"   -> answers is a map {header: chosen_label}
        #   "list"     -> answers is a list [chosen_label] (positional)
        self.answer_key = answer_key
        self.lines: list[str] = []
        self.session_id = ""
        self.proc: asyncio.subprocess.Process | None = None
        self.dialog_seen: list[dict] = []
        self.permission_seen: list[dict] = []
        self.responded_control_ids: set[str] = set()

    async def _write(self, obj: dict) -> None:
        assert self.proc and self.proc.stdin
        data = (json.dumps(obj) + "\n").encode()
        self.proc.stdin.write(data)
        await self.proc.stdin.drain()
        self._record_out(obj)

    def _record_out(self, obj: dict) -> None:
        self.lines.append(">>> SENT: " + json.dumps(obj))

    def _record_in(self, text: str) -> None:
        self.lines.append(text)

    async def _respond_dialog(self, raw: dict, request: dict) -> None:
        req_id = raw.get("request_id") or request.get("request_id", "")
        if req_id in self.responded_control_ids:
            return
        self.responded_control_ids.add(req_id)
        payload = request.get("payload", {}) or {}
        if self.scenario == "C_cancelled":
            inner = {"behavior": "cancelled"}
        else:
            inner = {"behavior": "completed", "result": build_completed_result(payload)}
        resp = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": req_id,
                "response": inner,
            },
        }
        await self._write(resp)

    async def _respond_permission(self, raw: dict, request: dict) -> None:
        req_id = raw.get("request_id") or request.get("request_id", "")
        if req_id in self.responded_control_ids:
            return
        self.responded_control_ids.add(req_id)
        # Echo the tool input back as updatedInput (both key spellings observed
        # across CLI versions: tool_input on the wire, input in SDK typings).
        updated = dict(request.get("input") or request.get("tool_input") or {})
        # E_answer: inject the user's answer into updatedInput. The hypothesis is
        # that AskUserQuestion reads its answers from an `answers` parameter that
        # the permission component is expected to fill.
        if self.scenario == "E_answer":
            updated["answers"] = self._build_answers(updated)
        resp = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": req_id,
                "response": {"behavior": "allow", "updatedInput": updated},
            },
        }
        await self._write(resp)

    def _build_answers(self, tool_input: dict):
        """Build the `answers` value for the AskUserQuestion updatedInput.

        We always choose "Red" for every question. Shape depends on answer_key.
        """
        questions = tool_input.get("questions") or []
        if isinstance(questions, dict):
            questions = [questions]

        def chosen_label(q: dict) -> str:
            opts = q.get("options") or []
            for o in opts:
                label = o.get("label") if isinstance(o, dict) else o
                if isinstance(label, str) and "red" in label.lower():
                    return label
            if opts:
                first = opts[0]
                return first.get("label") if isinstance(first, dict) else first
            return "Red"

        if self.answer_key == "list":
            return [chosen_label(q) for q in questions if isinstance(q, dict)]
        key_field = "header" if self.answer_key == "header" else "question"
        answers: dict = {}
        for q in questions:
            if not isinstance(q, dict):
                continue
            key = q.get(key_field) or q.get("question") or q.get("header")
            answers[key] = chosen_label(q)
        return answers

    async def _handle_control_request(self, raw: dict) -> None:
        request = raw.get("request", {}) or {}
        subtype = request.get("subtype", "")
        if subtype == "request_user_dialog":
            self.dialog_seen.append(raw)
            await self._respond_dialog(raw, request)
        elif subtype in ("permission", "can_use_tool"):
            self.permission_seen.append(raw)
            await self._respond_permission(raw, request)
        # else: hook lifecycle / other control_requests — just record, no reply.

    async def run(self) -> None:
        # KEY FINDING: --permission-prompt-tool stdio is what makes the CLI
        # enable interactive dialog tools (AskUserQuestion, EnterPlanMode,
        # ExitPlanMode) in tools[]. Without it they are stripped and
        # AskUserQuestion errors "not enabled in this context". So every
        # dialog/permission scenario needs it.
        wants_stdio = self.scenario in ("D", "B", "C_completed", "C_cancelled", "E_answer")
        argv = build_argv(
            "claude",
            self.model,
            permission_prompt_tool="stdio" if wants_stdio else None,
            # Restrict the allow-list so Bash is NOT pre-approved and MUST route
            # through the stdio permission prompt (claudestream's sandbox path).
            allowed_tools=["Read"] if self.scenario == "D" else None,
        )
        env = build_env()
        self.lines.append(f"### ARGV: {json.dumps(argv)}")
        self.lines.append(f"### SCENARIO: {self.scenario}  MODEL: {self.model}")
        self.proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16_777_216,
            env=env,
        )
        stderr_lines: list[str] = []

        async def drain_stderr() -> None:
            assert self.proc and self.proc.stderr
            while True:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                t = line.decode("utf-8", errors="replace").rstrip()
                if t:
                    stderr_lines.append(t)

        stderr_task = asyncio.create_task(drain_stderr())

        # Scenario D matches claudestream's sandbox path: no self-initialize,
        # just --permission-prompt-tool stdio + a restrictive --allowedTools.
        do_initialize = self.scenario in ("B", "C_completed", "C_cancelled", "E_answer")

        try:
            await asyncio.wait_for(self._drive(do_initialize), timeout=HARD_TIMEOUT)
        except asyncio.TimeoutError:
            self.lines.append("### HARD TIMEOUT reached — killing subprocess")
        finally:
            stderr_task.cancel()
            for s in stderr_lines:
                self.lines.append("STDERR: " + s)
            if self.proc.returncode is None:
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(self.proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
            self.lines.append(f"### EXIT CODE: {self.proc.returncode}")

    async def _drive(self, do_initialize: bool) -> None:
        if self.scenario == "D":
            prompt = BASH_PROMPT
        elif self.scenario == "A":
            prompt = COLOR_PROMPT
        else:
            prompt = COLOR_PROMPT_DIRECT

        if do_initialize:
            await self._write(initialize_frame())
            # Read until the initialize control_response arrives.
            await self._read_until(
                stop=lambda raw: raw.get("type") == "control_response"
                and (raw.get("response", {}) or {}).get("request_id") == "init_1",
                deadline=30.0,
            )

        await self._write(user_frame(prompt, self.session_id))

        # Read the remainder of the turn until a result message, handling any
        # control_requests (dialogs / permissions) as they arrive.
        await self._read_until(
            stop=lambda raw: raw.get("type") == "result",
            deadline=HARD_TIMEOUT,
        )
        # Give a brief grace period to capture any trailing frames.
        await self._read_for(2.0)

    async def _read_until(self, stop, deadline: float) -> None:
        start = time.monotonic()
        assert self.proc and self.proc.stdout
        while True:
            remaining = deadline - (time.monotonic() - start)
            if remaining <= 0:
                return
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            self._record_in(text)
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                continue
            if raw.get("type") == "system" and not self.session_id:
                sid = raw.get("session_id")
                if sid:
                    self.session_id = sid
            if raw.get("type") == "control_request":
                await self._handle_control_request(raw)
            if stop(raw):
                return

    async def _read_for(self, seconds: float) -> None:
        start = time.monotonic()
        assert self.proc and self.proc.stdout
        while time.monotonic() - start < seconds:
            remaining = seconds - (time.monotonic() - start)
            try:
                line = await asyncio.wait_for(self.proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                return
            if not line:
                return
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            self._record_in(text)
            try:
                raw = json.loads(text)
            except json.JSONDecodeError:
                continue
            if raw.get("type") == "control_request":
                await self._handle_control_request(raw)


async def main_async(args: argparse.Namespace) -> None:
    probe = Probe(args.scenario, args.model, answer_key=args.answer_key)
    await probe.run()
    out = CAPTURES_DIR / f"scenario_{args.scenario}.ndjson"
    out.write_text("\n".join(probe.lines) + "\n")
    print(f"scenario={args.scenario} model={args.model}")
    print(f"lines_captured={len(probe.lines)}")
    print(f"dialogs_seen={len(probe.dialog_seen)}")
    print(f"permissions_seen={len(probe.permission_seen)}")
    print(f"session_id={probe.session_id}")
    print(f"capture={out}")
    if probe.dialog_seen:
        print("--- FIRST DIALOG REQUEST ---")
        print(json.dumps(probe.dialog_seen[0], indent=2))
    if probe.permission_seen:
        print("--- FIRST PERMISSION REQUEST ---")
        print(json.dumps(probe.permission_seen[0], indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--scenario",
        required=True,
        choices=["A", "B", "C_completed", "C_cancelled", "D", "E_answer"],
    )
    ap.add_argument("--model", default="haiku")
    ap.add_argument(
        "--answer-key",
        default="question",
        choices=["question", "header", "list"],
        help="E_answer: how to key/shape the injected answers on updatedInput",
    )
    args = ap.parse_args()
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
