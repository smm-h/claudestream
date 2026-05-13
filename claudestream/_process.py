"""Subprocess management for Claude Code CLI."""

from __future__ import annotations

import asyncio
import atexit
import logging
import re
import shutil
import signal
from dataclasses import dataclass, field

log = logging.getLogger("claudestream")

# Track active child processes for atexit cleanup
_ACTIVE_CHILDREN: set[asyncio.subprocess.Process] = set()


def _kill_active_children():
    """atexit handler: SIGTERM all tracked child processes."""
    for proc in _ACTIVE_CHILDREN:
        try:
            proc.send_signal(signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass


atexit.register(_kill_active_children)

MINIMUM_CLAUDE_VERSION = "2.0.0"


def find_binary(binary: str | None = None) -> str:
    """Locate the claude CLI binary.

    Args:
        binary: Explicit path to claude binary. If None, searches PATH.

    Returns:
        Absolute path to the claude binary.

    Raises:
        FileNotFoundError: If claude is not found.
    """
    if binary:
        return binary
    found = shutil.which("claude")
    if not found:
        raise FileNotFoundError(
            "claude CLI not found on PATH. Install it or pass binary= explicitly."
        )
    return found


def _version_lt(a: str, b: str) -> bool:
    """Return True if version a < version b (semver comparison)."""

    def parts(v: str) -> tuple[int, ...]:
        return tuple(int(x) for x in v.split("."))

    return parts(a) < parts(b)


async def check_version(binary: str) -> str | None:
    """Check claude CLI version. Logs warning if below minimum. Returns version string or None."""
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-v",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        output = stdout.decode("utf-8", errors="replace").strip()
        # Parse version from output like "claude-code 2.1.128" or just "2.1.128"
        match = re.search(r"(\d+\.\d+\.\d+)", output)
        if not match:
            log.debug("could not parse version from: %s", output)
            return None
        version = match.group(1)
        if _version_lt(version, MINIMUM_CLAUDE_VERSION):
            log.warning(
                "claude %s is below minimum %s — some features may not work",
                version,
                MINIMUM_CLAUDE_VERSION,
            )
        return version
    except (asyncio.TimeoutError, FileNotFoundError, OSError) as e:
        log.debug("version check failed: %s", e)
        return None


@dataclass
class ProcessConfig:
    """Configuration for spawning a Claude Code subprocess."""

    binary: str = "claude"
    cwd: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_prompt_tool: str | None = None  # "stdio" when using callback policy
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None

    def build_argv(self) -> list[str]:
        """Build the full command-line argument list."""
        argv = [
            self.binary,
            "--output-format",
            "stream-json",
            "--input-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
        ]
        if self.model:
            argv.extend(["--model", self.model])
        if self.system_prompt is not None:
            argv.extend(["--system-prompt", self.system_prompt])
        if self.permission_mode:
            argv.extend(["--permission-mode", self.permission_mode])
        if self.allowed_tools:
            argv.extend(["--allowedTools", ",".join(self.allowed_tools)])
        if self.disallowed_tools:
            argv.extend(["--disallowedTools", ",".join(self.disallowed_tools)])
        if self.permission_prompt_tool:
            argv.extend(["--permission-prompt-tool", self.permission_prompt_tool])
        argv.extend(self.extra_args)
        return argv


class ProcessManager:
    """Manages a Claude Code subprocess lifecycle."""

    def __init__(self, config: ProcessConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None

    @property
    def stdin(self) -> asyncio.StreamWriter:
        assert self._process and self._process.stdin
        return self._process.stdin

    @property
    def stdout(self) -> asyncio.StreamReader:
        assert self._process and self._process.stdout
        return self._process.stdout

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> None:
        """Spawn the claude subprocess."""
        argv = self.config.build_argv()
        log.debug("spawning: %s", " ".join(argv))

        env = None
        if self.config.env:
            import os

            env = {**os.environ, **self.config.env}

        self._process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.config.cwd,
            env=env,
        )
        _ACTIVE_CHILDREN.add(self._process)
        log.debug("claude process started: pid=%d", self._process.pid)

    async def close(self) -> None:
        """Graceful shutdown: close stdin -> 5s wait -> SIGTERM -> 5s wait -> SIGKILL."""
        if not self._process:
            return

        proc = self._process
        self._process = None
        _ACTIVE_CHILDREN.discard(proc)

        # Close stdin
        if proc.stdin:
            try:
                proc.stdin.close()
                await proc.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        # Wait for graceful exit
        if proc.returncode is None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

        # SIGTERM
        if proc.returncode is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

        # SIGKILL
        if proc.returncode is None:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass

        log.debug("claude process terminated: returncode=%s", proc.returncode)

    async def kill(self) -> None:
        """Immediate kill."""
        if not self._process:
            return
        proc = self._process
        self._process = None
        _ACTIVE_CHILDREN.discard(proc)
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass
