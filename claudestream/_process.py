"""Subprocess management for launching and monitoring the Claude Code CLI process, including graceful shutdown and atexit cleanup."""

from __future__ import annotations

import asyncio
import atexit
import logging
import re
import shutil
import signal
import msgspec

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


# Declarative flag registry: (field_name, cli_flag, style)
# Styles: "value" emits [--flag, str(val)], "bool" emits [--flag], "list" emits [--flag, ",".join(val)]
# debug/debug_filter handled specially outside this registry.
_FLAG_REGISTRY: list[tuple[str, str, str]] = [
    # Value flags
    ("model", "--model", "value"),
    ("system_prompt", "--system-prompt", "value"),
    ("permission_mode", "--permission-mode", "value"),
    ("permission_prompt_tool", "--permission-prompt-tool", "value"),
    ("resume_session_id", "--resume", "value"),
    ("effort", "--effort", "value"),
    ("json_schema_str", "--json-schema", "value"),
    ("max_budget_usd", "--max-budget-usd", "value"),
    ("fallback_model", "--fallback-model", "value"),
    ("name", "--name", "value"),
    ("setting_sources", "--setting-sources", "value"),
    ("settings", "--settings", "value"),
    ("debug_file", "--debug-file", "value"),
    ("agent", "--agent", "value"),
    ("agents_json", "--agents", "value"),
    ("remote_control", "--remote-control", "value"),
    ("remote_control_prefix", "--remote-control-session-name-prefix", "value"),
    ("worktree", "--worktree", "value"),
    ("from_pr", "--from-pr", "value"),
    ("session_id", "--session-id", "value"),
    # List flags
    ("allowed_tools", "--allowedTools", "list"),
    ("disallowed_tools", "--disallowedTools", "list"),
    ("betas", "--betas", "list"),
    ("add_dirs", "--add-dir", "list"),
    ("builtin_tools", "--tools", "list"),
    ("file_specs", "--file", "list"),
    ("mcp_config", "--mcp-config", "list"),
    ("plugin_dirs", "--plugin-dir", "list"),
    ("plugin_urls", "--plugin-url", "list"),
    # Bool flags
    ("bare", "--bare", "bool"),
    ("brief", "--brief", "bool"),
    ("continue_session", "--continue", "bool"),
    ("fork_session", "--fork-session", "bool"),
    ("no_session_persistence", "--no-session-persistence", "bool"),
    ("strict_mcp_config", "--strict-mcp-config", "bool"),
    ("include_hook_events", "--include-hook-events", "bool"),
    ("replay_user_messages", "--replay-user-messages", "bool"),
    ("exclude_dynamic_prompt_sections", "--exclude-dynamic-system-prompt-sections", "bool"),
    ("disable_slash_commands", "--disable-slash-commands", "bool"),
    ("chrome", "--chrome", "bool"),
    ("ide", "--ide", "bool"),
    ("tmux", "--tmux", "bool"),
    ("verbose", "--verbose", "bool"),
    ("include_partial_messages", "--include-partial-messages", "bool"),
]


class ProcessConfig(msgspec.Struct, frozen=True):
    """Configuration for spawning a Claude Code subprocess."""

    binary: str = "claude"
    cwd: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    permission_mode: str | None = None
    allowed_tools: list[str] = []
    disallowed_tools: list[str] = []
    permission_prompt_tool: str | None = None  # "stdio" when sandbox needs permission interception
    resume_session_id: str | None = None
    extra_args: list[str] = []
    env: dict[str, str] | None = None

    # --- String value flags (--flag value) ---
    effort: str | None = None
    json_schema_str: str | None = None  # passed as --json-schema
    fallback_model: str | None = None
    name: str | None = None
    setting_sources: str | None = None
    settings: str | None = None
    debug_filter: str | None = None  # --debug <filter_pattern>
    debug_file: str | None = None
    agent: str | None = None
    agents_json: str | None = None  # --agents
    remote_control: str | None = None
    remote_control_prefix: str | None = None  # --remote-control-session-name-prefix
    worktree: str | None = None
    from_pr: str | None = None
    session_id: str | None = None

    # --- List flags (--flag value, repeatable) ---
    betas: list[str] = []
    add_dirs: list[str] = []  # --add-dir
    builtin_tools: list[str] = []  # --tools
    file_specs: list[str] = []  # --file
    mcp_config: list[str] = []  # --mcp-config
    plugin_dirs: list[str] = []  # --plugin-dir
    plugin_urls: list[str] = []  # --plugin-url

    # --- Bool flags ---
    bare: bool = False
    brief: bool = False
    continue_session: bool = False  # --continue
    fork_session: bool = False
    no_session_persistence: bool = False
    strict_mcp_config: bool = False
    include_hook_events: bool = False
    replay_user_messages: bool = False
    exclude_dynamic_prompt_sections: bool = False  # --exclude-dynamic-system-prompt-sections
    disable_slash_commands: bool = False
    chrome: bool = False
    ide: bool = False
    tmux: bool = False
    debug: bool = False  # --debug (bool component; True means pass --debug)

    # --- Float flag ---
    max_budget_usd: float | None = None

    # --- Currently hardcoded, now configurable ---
    verbose: bool = True  # --verbose (default True)
    include_partial_messages: bool = True  # --include-partial-messages (default True)

    # --- Process-level tuning (not CLI flags, used by ProcessManager) ---
    buffer_limit: int = 16_777_216
    shutdown_timeout: float = 5.0

    # --- Hooks (passed to InitializeRequest, not a CLI flag) ---
    hooks: dict = {}

    def build_argv(self) -> list[str]:
        """Build the full command-line argument list from the flag registry."""
        argv = [self.binary, "--output-format", "stream-json", "--input-format", "stream-json"]
        for field_name, flag, style in _FLAG_REGISTRY:
            value = getattr(self, field_name)
            if style == "value" and value:
                argv.extend([flag, str(value)])
            elif style == "bool" and value:
                argv.append(flag)
            elif style == "list" and value:
                argv.extend([flag, ",".join(value)])
        # Special case: --debug can be a bare flag or take a filter argument.
        # debug_filter is not in the registry; handled here to avoid duplicate --debug.
        if self.debug and self.debug_filter:
            argv.extend(["--debug", self.debug_filter])
        elif self.debug:
            argv.append("--debug")
        elif self.debug_filter:
            # Filter set without debug=True: emit --debug <filter> anyway
            argv.extend(["--debug", self.debug_filter])
        argv.extend(self.extra_args)
        return argv


class ProcessManager:
    """Manages a Claude Code subprocess lifecycle."""

    def __init__(self, config: ProcessConfig):
        self.config = config
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_lines: list[str] = []
        self._stderr_task: asyncio.Task | None = None

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

    @property
    def stderr_lines(self) -> list[str]:
        return list(self._stderr_lines)

    async def _drain_stderr(self) -> None:
        """Read stderr line-by-line to prevent pipe buffer deadlock."""
        assert self._process and self._process.stderr
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                log.warning("claude stderr: %s", text)
                self._stderr_lines.append(text)

    async def start(self) -> None:
        """Spawn the claude subprocess."""
        argv = self.config.build_argv()
        log.info(
            "spawning: binary=%s cwd=%s extra_args=%s",
            self.config.binary,
            self.config.cwd,
            self.config.extra_args,
        )

        env = None
        if self.config.env:
            import os

            env = {**os.environ, **self.config.env}

        self._process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,  # 16MB — extended thinking produces huge NDJSON lines
            cwd=self.config.cwd,
            env=env,
        )
        _ACTIVE_CHILDREN.add(self._process)
        log.info("claude process started: pid=%d", self._process.pid)
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def close(self) -> None:
        """Graceful shutdown: close stdin -> 5s wait -> SIGTERM -> 5s wait -> SIGKILL."""
        if not self._process:
            return

        proc = self._process
        self._process = None
        _ACTIVE_CHILDREN.discard(proc)

        # Cancel stderr drain task
        if self._stderr_task is not None and not self._stderr_task.done():
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
        self._stderr_task = None

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

        log.info("claude process terminated: returncode=%s", proc.returncode)

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
