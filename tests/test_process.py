"""Tests for subprocess management."""

from claudestream._process import ProcessConfig, find_binary, _version_lt


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
        idx = argv.index("--system-prompt")
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

    def test_no_optional_flags_when_unset(self):
        config = ProcessConfig(binary="claude")
        argv = config.build_argv()
        assert "--model" not in argv
        assert "--system-prompt" not in argv
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
