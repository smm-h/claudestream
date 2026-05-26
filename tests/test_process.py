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
        config = ProcessConfig(binary="claude", system_prompt="You are helpful.")
        argv = config.build_argv()
        idx = argv.index("--system-prompt")
        assert argv[idx + 1] == "You are helpful."

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
        assert "--system-prompt" in argv

    def test_no_optional_flags_when_unset(self):
        config = ProcessConfig(binary="claude")
        argv = config.build_argv()
        assert "--model" not in argv
        assert "--system-prompt" not in argv
        assert "--permission-mode" not in argv
        assert "--allowedTools" not in argv

    def test_effort_flag(self):
        config = ProcessConfig(binary="claude", effort="high")
        argv = config.build_argv()
        idx = argv.index("--effort")
        assert argv[idx + 1] == "high"

    def test_json_schema_flag(self):
        config = ProcessConfig(binary="claude", json_schema_str='{"type":"object"}')
        argv = config.build_argv()
        idx = argv.index("--json-schema")
        assert argv[idx + 1] == '{"type":"object"}'

    def test_max_budget_usd_flag(self):
        config = ProcessConfig(binary="claude", max_budget_usd=10.5)
        argv = config.build_argv()
        idx = argv.index("--max-budget-usd")
        assert argv[idx + 1] == "10.5"

    def test_bare_flag(self):
        config = ProcessConfig(binary="claude", bare=True)
        argv = config.build_argv()
        assert "--bare" in argv

    def test_bare_flag_absent_when_false(self):
        config = ProcessConfig(binary="claude", bare=False)
        argv = config.build_argv()
        assert "--bare" not in argv

    def test_mcp_config_flag(self):
        config = ProcessConfig(binary="claude", mcp_config=["mcp1.json", "mcp2.json"])
        argv = config.build_argv()
        idx = argv.index("--mcp-config")
        assert argv[idx + 1] == "mcp1.json,mcp2.json"

    def test_betas_flag(self):
        config = ProcessConfig(binary="claude", betas=["beta1", "beta2"])
        argv = config.build_argv()
        idx = argv.index("--betas")
        assert argv[idx + 1] == "beta1,beta2"

    def test_verbose_true_includes_flag(self):
        config = ProcessConfig(binary="claude", verbose=True)
        argv = config.build_argv()
        assert "--verbose" in argv

    def test_verbose_false_omits_flag(self):
        config = ProcessConfig(binary="claude", verbose=False)
        argv = config.build_argv()
        assert "--verbose" not in argv

    def test_include_partial_messages_true_includes_flag(self):
        config = ProcessConfig(binary="claude", include_partial_messages=True)
        argv = config.build_argv()
        assert "--include-partial-messages" in argv

    def test_include_partial_messages_false_omits_flag(self):
        config = ProcessConfig(binary="claude", include_partial_messages=False)
        argv = config.build_argv()
        assert "--include-partial-messages" not in argv

    def test_debug_bool_only(self):
        config = ProcessConfig(binary="claude", debug=True)
        argv = config.build_argv()
        assert "--debug" in argv

    def test_debug_with_filter(self):
        config = ProcessConfig(binary="claude", debug=True, debug_filter="transport")
        argv = config.build_argv()
        idx = argv.index("--debug")
        assert argv[idx + 1] == "transport"

    def test_debug_filter_without_debug_bool(self):
        """debug_filter set but debug=False still emits --debug <filter>."""
        config = ProcessConfig(binary="claude", debug=False, debug_filter="transport")
        argv = config.build_argv()
        idx = argv.index("--debug")
        assert argv[idx + 1] == "transport"

    def test_debug_false_no_filter_omits_flag(self):
        config = ProcessConfig(binary="claude", debug=False)
        argv = config.build_argv()
        assert "--debug" not in argv

    def test_new_fields_stored_correctly(self):
        """New typed fields are stored when passed as keyword args."""
        config = ProcessConfig(
            binary="claude",
            effort="high",
            json_schema_str='{"type":"object"}',
            fallback_model="sonnet",
            name="my-session",
            setting_sources="project",
            settings="/tmp/settings.json",
            debug_filter="transport",
            debug_file="/tmp/debug.log",
            agent="coder",
            agents_json="/tmp/agents.json",
            remote_control="rc-1",
            remote_control_prefix="prefix-",
            worktree="/home/user/repo",
            from_pr="https://github.com/org/repo/pull/1",
            session_id="sess-abc",
            betas=["beta1", "beta2"],
            add_dirs=["/extra/dir"],
            builtin_tools=["Read", "Write"],
            file_specs=["file1.py", "file2.py"],
            mcp_config=["mcp.json"],
            plugin_dirs=["/plugins"],
            plugin_urls=["https://example.com/plugin"],
            bare=True,
            brief=True,
            continue_session=True,
            fork_session=True,
            no_session_persistence=True,
            strict_mcp_config=True,
            include_hook_events=True,
            replay_user_messages=True,
            exclude_dynamic_prompt_sections=True,
            disable_slash_commands=True,
            chrome=True,
            ide=True,
            tmux=True,
            debug=True,
            max_budget_usd=10.5,
            verbose=False,
            include_partial_messages=False,
            buffer_limit=8_000_000,
            shutdown_timeout=10.0,
            hooks={"on_start": "echo hi"},
        )
        assert config.effort == "high"
        assert config.json_schema_str == '{"type":"object"}'
        assert config.fallback_model == "sonnet"
        assert config.name == "my-session"
        assert config.setting_sources == "project"
        assert config.settings == "/tmp/settings.json"
        assert config.debug_filter == "transport"
        assert config.debug_file == "/tmp/debug.log"
        assert config.agent == "coder"
        assert config.agents_json == "/tmp/agents.json"
        assert config.remote_control == "rc-1"
        assert config.remote_control_prefix == "prefix-"
        assert config.worktree == "/home/user/repo"
        assert config.from_pr == "https://github.com/org/repo/pull/1"
        assert config.session_id == "sess-abc"
        assert config.betas == ["beta1", "beta2"]
        assert config.add_dirs == ["/extra/dir"]
        assert config.builtin_tools == ["Read", "Write"]
        assert config.file_specs == ["file1.py", "file2.py"]
        assert config.mcp_config == ["mcp.json"]
        assert config.plugin_dirs == ["/plugins"]
        assert config.plugin_urls == ["https://example.com/plugin"]
        assert config.bare is True
        assert config.brief is True
        assert config.continue_session is True
        assert config.fork_session is True
        assert config.no_session_persistence is True
        assert config.strict_mcp_config is True
        assert config.include_hook_events is True
        assert config.replay_user_messages is True
        assert config.exclude_dynamic_prompt_sections is True
        assert config.disable_slash_commands is True
        assert config.chrome is True
        assert config.ide is True
        assert config.tmux is True
        assert config.debug is True
        assert config.max_budget_usd == 10.5
        assert config.verbose is False
        assert config.include_partial_messages is False
        assert config.buffer_limit == 8_000_000
        assert config.shutdown_timeout == 10.0
        assert config.hooks == {"on_start": "echo hi"}

    def test_new_fields_default_values(self):
        """All new fields have correct defaults when not specified."""
        config = ProcessConfig()
        # String fields default to None
        assert config.effort is None
        assert config.json_schema_str is None
        assert config.fallback_model is None
        assert config.name is None
        assert config.setting_sources is None
        assert config.settings is None
        assert config.debug_filter is None
        assert config.debug_file is None
        assert config.agent is None
        assert config.agents_json is None
        assert config.remote_control is None
        assert config.remote_control_prefix is None
        assert config.worktree is None
        assert config.from_pr is None
        assert config.session_id is None
        # List fields default to []
        assert config.betas == []
        assert config.add_dirs == []
        assert config.builtin_tools == []
        assert config.file_specs == []
        assert config.mcp_config == []
        assert config.plugin_dirs == []
        assert config.plugin_urls == []
        # Bool flags default to False
        assert config.bare is False
        assert config.brief is False
        assert config.continue_session is False
        assert config.fork_session is False
        assert config.no_session_persistence is False
        assert config.strict_mcp_config is False
        assert config.include_hook_events is False
        assert config.replay_user_messages is False
        assert config.exclude_dynamic_prompt_sections is False
        assert config.disable_slash_commands is False
        assert config.chrome is False
        assert config.ide is False
        assert config.tmux is False
        assert config.debug is False
        # Float field defaults to None
        assert config.max_budget_usd is None
        # Hardcoded-now-configurable default to True
        assert config.verbose is True
        assert config.include_partial_messages is True
        # Process-level tuning
        assert config.buffer_limit == 16_777_216
        assert config.shutdown_timeout == 5.0
        # Hooks
        assert config.hooks == {}


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
