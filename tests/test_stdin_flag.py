"""Tests for the --stdin flag on send, stream, and events commands."""

from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

from claudestream._cli import app, cmd_send, cmd_stream, cmd_events


COMMANDS = ["send", "stream", "events"]
CMD_FUNCS = [cmd_send, cmd_stream, cmd_events]


def _mock_sync_session():
    """Create a mock SyncSession context manager with empty event stream."""
    session = MagicMock()
    session.send.return_value = []
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestStdinReadsInput:
    @pytest.mark.parametrize("cmd_func", CMD_FUNCS, ids=COMMANDS)
    @patch("claudestream._cli.SyncSession")
    def test_stdin_reads_from_stdin(self, mock_cls, cmd_func, capsys):
        mock_cls.return_value = _mock_sync_session()
        with patch("sys.stdin", StringIO("hello from pipe\n")):
            result = cmd_func(stdin=True, model="sonnet", profile="test")
        assert result != 1
        # Verify the prompt was passed to session.send
        mock_cls.return_value.__enter__.return_value.send.assert_called_once()
        call_args = mock_cls.return_value.__enter__.return_value.send.call_args
        assert call_args[0][0] == "hello from pipe"


class TestStdinAndPromptConflict:
    @pytest.mark.parametrize("cmd_func", CMD_FUNCS, ids=COMMANDS)
    def test_error_when_both_prompt_and_stdin(self, cmd_func, capsys):
        result = cmd_func(prompt="hello", stdin=True, model="sonnet", profile="test")
        assert result == 1
        assert "cannot use both prompt argument and --stdin" in capsys.readouterr().err


class TestStdinEmpty:
    @pytest.mark.parametrize("cmd_func", CMD_FUNCS, ids=COMMANDS)
    def test_error_when_stdin_is_empty(self, cmd_func, capsys):
        with patch("sys.stdin", StringIO("")):
            result = cmd_func(stdin=True, model="sonnet", profile="test")
        assert result == 1
        assert "--stdin provided but stdin is empty" in capsys.readouterr().err

    @pytest.mark.parametrize("cmd_func", CMD_FUNCS, ids=COMMANDS)
    def test_error_when_stdin_is_whitespace_only(self, cmd_func, capsys):
        with patch("sys.stdin", StringIO("   \n\n  ")):
            result = cmd_func(stdin=True, model="sonnet", profile="test")
        assert result == 1
        assert "--stdin provided but stdin is empty" in capsys.readouterr().err


class TestNoPromptNoStdin:
    @pytest.mark.parametrize("cmd_func", CMD_FUNCS, ids=COMMANDS)
    def test_error_when_no_prompt_and_no_stdin(self, cmd_func, capsys):
        result = cmd_func(model="sonnet", profile="test")
        assert result == 1
        assert "prompt argument required (or use --stdin)" in capsys.readouterr().err


class TestNormalPromptStillWorks:
    @pytest.mark.parametrize("cmd_func", CMD_FUNCS, ids=COMMANDS)
    @patch("claudestream._cli.SyncSession")
    def test_prompt_argument_works(self, mock_cls, cmd_func, capsys):
        mock_cls.return_value = _mock_sync_session()
        result = cmd_func(prompt="hello", model="sonnet", profile="test")
        assert result != 1
        mock_cls.return_value.__enter__.return_value.send.assert_called_once()
        call_args = mock_cls.return_value.__enter__.return_value.send.call_args
        assert call_args[0][0] == "hello"


class TestStdinCliParsing:
    @pytest.mark.parametrize("command", COMMANDS)
    @patch("claudestream._cli.SyncSession")
    def test_stdin_flag_parsed_by_strictcli(self, mock_cls, command):
        """Verify strictcli recognizes --stdin on each command."""
        mock_cls.return_value = _mock_sync_session()
        with patch("sys.stdin", StringIO("piped prompt\n")):
            args = [command, "--stdin", "--model", "opus", "--profile", "test"]
            result = app.test(args)
        assert "unknown" not in result.stderr.lower()

    def test_repl_does_not_have_stdin_flag(self):
        """Verify --stdin is NOT recognized on the repl command."""
        result = app.test(["repl", "--stdin", "--model", "opus", "--profile", "test"])
        assert result.exit_code != 0
