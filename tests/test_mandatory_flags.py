"""Tests that --profile and --model are required flags on all CLI commands."""

from unittest.mock import patch, MagicMock

import pytest

from claudestream._cli import app


# Commands that take a positional prompt argument
PROMPT_COMMANDS = ["send", "stream", "events"]

# Commands with no positional arguments
NO_ARG_COMMANDS = ["repl"]

ALL_COMMANDS = PROMPT_COMMANDS + NO_ARG_COMMANDS


def _base_args(command: str) -> list[str]:
    """Return the minimal argv for a command (excluding --model and --profile)."""
    if command in PROMPT_COMMANDS:
        return [command, "hello"]
    return [command]


class TestModelRequired:
    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_missing_model_fails(self, command):
        """Each command fails with exit code 1 when --model is missing."""
        args = _base_args(command) + ["--profile", "test"]
        result = app.test(args)
        assert result.exit_code == 1
        assert "--model" in result.stderr
        assert "required" in result.stderr


class TestProfileRequired:
    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_missing_profile_fails(self, command):
        """Each command fails with exit code 1 when --profile is missing."""
        args = _base_args(command) + ["--model", "opus"]
        result = app.test(args)
        assert result.exit_code == 1
        assert "--profile" in result.stderr
        assert "required" in result.stderr


class TestBothMissing:
    @pytest.mark.parametrize("command", ALL_COMMANDS)
    def test_both_missing_fails(self, command):
        """Each command fails when both --model and --profile are missing."""
        args = _base_args(command)
        result = app.test(args)
        assert result.exit_code == 1
        # At least one of the flags should be mentioned
        assert "--model" in result.stderr or "--profile" in result.stderr


class TestBothProvided:
    @pytest.mark.parametrize("command", ALL_COMMANDS)
    @patch("claudestream._cli.SyncSession")
    def test_both_provided_parses(self, mock_cls, command):
        """Providing both --model and --profile passes CLI parsing.

        SyncSession is mocked to prevent real execution. We verify that
        strictcli does not emit a 'required' parse error.
        """
        session = MagicMock()
        session.send.return_value = []
        session.model_name = "test-model"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = ctx

        # For repl, mock input to immediately raise EOFError (exit)
        if command == "repl":
            with patch("builtins.input", side_effect=EOFError):
                args = _base_args(command) + ["--model", "opus", "--profile", "lisa"]
                result = app.test(args)
        else:
            args = _base_args(command) + ["--model", "opus", "--profile", "lisa"]
            result = app.test(args)

        # Parsing succeeded -- no "required" error
        assert "required" not in result.stderr
