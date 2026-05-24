"""Tests for subprocess management."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from claudestream._process import ProcessConfig, ProcessManager, find_binary, _version_lt


class TestProcessConfig:
    def test_minimal_argv(self):
        config = ProcessConfig(binary="/usr/bin/claude")
        argv = config.build_argv()
        assert argv[0] == "/usr/bin/claude"
        assert "--output-format" in argv
        assert "stream-json" in argv
        assert "--input-format" in argv
        assert "--verbose" in argv
        assert "--include-partial-messages" in argv

    def test_model_flag(self):
        config = ProcessConfig(binary="claude", model="opus")
        argv = config.build_argv()
        idx = argv.index("--model")
        assert argv[idx + 1] == "opus"

    def test_system_prompt_flag(self):
        config = ProcessConfig(binary="claude", system_prompt="")
        argv = config.build_argv()
        idx = argv.index("--append-system-prompt")
        assert argv[idx + 1] == ""

    def test_allowed_tools_comma_joined(self):
        config = ProcessConfig(binary="claude", allowed_tools=["Read", "Write", "Bash"])
        argv = config.build_argv()
        idx = argv.index("--allowedTools")
        assert argv[idx + 1] == "Read,Write,Bash"

    def test_permission_prompt_tool(self):
        config = ProcessConfig(binary="claude", permission_prompt_tool="stdio")
        argv = config.build_argv()
        idx = argv.index("--permission-prompt-tool")
        assert argv[idx + 1] == "stdio"

    def test_extra_args_appended(self):
        config = ProcessConfig(binary="claude", extra_args=["--max-turns", "5"])
        argv = config.build_argv()
        assert argv[-2] == "--max-turns"
        assert argv[-1] == "5"

    def test_no_bare_flag_with_system_prompt(self):
        config = ProcessConfig(binary="claude", system_prompt="You are a game agent.")
        argv = config.build_argv()
        assert "--bare" not in argv
        assert "--append-system-prompt" in argv

    def test_no_optional_flags_when_unset(self):
        config = ProcessConfig(binary="claude")
        argv = config.build_argv()
        assert "--model" not in argv
        assert "--append-system-prompt" not in argv
        assert "--permission-mode" not in argv
        assert "--allowedTools" not in argv


class TestVersionLt:
    def test_less_than(self):
        assert _version_lt("1.9.9", "2.0.0")
        assert _version_lt("2.0.0", "2.0.1")
        assert _version_lt("2.0.0", "2.1.0")

    def test_not_less_than(self):
        assert not _version_lt("2.0.0", "2.0.0")
        assert not _version_lt("2.1.0", "2.0.0")
        assert not _version_lt("3.0.0", "2.0.0")


class TestFindBinary:
    def test_explicit_path(self):
        assert find_binary("/usr/local/bin/claude") == "/usr/local/bin/claude"

    def test_none_searches_path(self):
        # This test just verifies it doesn't crash -- claude may or may not be on PATH
        try:
            result = find_binary(None)
            assert isinstance(result, str)
        except FileNotFoundError:
            pass  # Expected if claude is not on PATH


class TestProcessManagerBufferLimit:
    """Test that ProcessManager.start() passes a 16MB buffer limit.

    Bug fix: extended thinking produces huge NDJSON lines that exceed asyncio's
    default 64KB buffer. The fix passes limit=16*1024*1024 to
    create_subprocess_exec.
    """

    def test_start_passes_16mb_limit(self):
        """Verify create_subprocess_exec receives limit=16*1024*1024."""
        config = ProcessConfig(binary="/fake/claude")
        manager = ProcessManager(config)

        captured_kwargs = {}

        async def run():
            stderr_reader = asyncio.StreamReader()
            stderr_reader.feed_eof()

            mock_process = MagicMock()
            mock_process.pid = 12345
            mock_process.stderr = stderr_reader

            async def fake_create_subprocess_exec(*args, **kwargs):
                captured_kwargs.update(kwargs)
                return mock_process

            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
                await manager.start()

        asyncio.run(run())

        expected_limit = 16 * 1024 * 1024  # 16MB
        assert "limit" in captured_kwargs, "limit kwarg not passed to create_subprocess_exec"
        assert captured_kwargs["limit"] == expected_limit, (
            f"Expected limit={expected_limit}, got limit={captured_kwargs['limit']}"
        )

    def test_start_uses_pipe_for_stdio(self):
        """Verify create_subprocess_exec uses PIPE for stdin, stdout, and stderr."""
        config = ProcessConfig(binary="/fake/claude")
        manager = ProcessManager(config)

        captured_kwargs = {}

        async def run():
            stderr_reader = asyncio.StreamReader()
            stderr_reader.feed_eof()

            mock_process = MagicMock()
            mock_process.pid = 99999
            mock_process.stderr = stderr_reader

            async def fake_create_subprocess_exec(*args, **kwargs):
                captured_kwargs.update(kwargs)
                return mock_process

            with patch("asyncio.create_subprocess_exec", side_effect=fake_create_subprocess_exec):
                await manager.start()

        asyncio.run(run())

        assert captured_kwargs["stdin"] == asyncio.subprocess.PIPE
        assert captured_kwargs["stdout"] == asyncio.subprocess.PIPE
        assert captured_kwargs["stderr"] == asyncio.subprocess.PIPE


class TestStderrDrain:
    """Tests for stderr drain coroutine that prevents pipe buffer deadlock."""

    def test_stderr_drain_accumulates_lines(self):
        """Feed data into a real StreamReader as stderr; verify lines are captured."""
        config = ProcessConfig(binary="/fake/claude")
        manager = ProcessManager(config)

        async def run():
            # Create a real StreamReader to act as stderr
            stderr_reader = asyncio.StreamReader()
            stderr_reader.feed_data(b"warning: something happened\n")
            stderr_reader.feed_data(b"error: bad thing\n")
            stderr_reader.feed_data(b"\n")  # empty line, should be skipped
            stderr_reader.feed_data(b"info: another line\n")
            stderr_reader.feed_eof()

            mock_process = MagicMock()
            mock_process.pid = 11111
            mock_process.stderr = stderr_reader

            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                await manager.start()

            # Wait for the drain task to finish (EOF fed above)
            assert manager._stderr_task is not None
            await manager._stderr_task

            lines = manager.stderr_lines
            assert len(lines) == 3
            assert lines[0] == "warning: something happened"
            assert lines[1] == "error: bad thing"
            assert lines[2] == "info: another line"

        asyncio.run(run())

    def test_stderr_task_cancelled_on_close(self):
        """Start manager with hanging stderr, close it, verify task is cancelled."""
        config = ProcessConfig(binary="/fake/claude")
        manager = ProcessManager(config)

        async def run():
            # StreamReader that never gets EOF — simulates hanging stderr
            stderr_reader = asyncio.StreamReader()

            mock_process = MagicMock()
            mock_process.pid = 22222
            mock_process.stderr = stderr_reader
            mock_process.returncode = None
            mock_process.stdin = None

            # Make wait() return immediately so close() doesn't hang
            async def fake_wait():
                mock_process.returncode = 0
                return 0

            mock_process.wait = fake_wait
            mock_process.send_signal = MagicMock()
            mock_process.kill = MagicMock()

            with patch("asyncio.create_subprocess_exec", return_value=mock_process):
                await manager.start()

            assert manager._stderr_task is not None
            assert not manager._stderr_task.done()

            await manager.close()

            assert manager._stderr_task is None

        asyncio.run(run())
