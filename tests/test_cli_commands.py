"""Tests for ask, doctor, and config CLI commands."""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from claudestream._cli import cmd_ask, cmd_doctor, cmd_config
from claudestream import ClaudeStreamError
from claudestream.events import AskResult, Usage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_sync_session_with_ask(ask_result):
    """Create a mock SyncSession context manager that returns ask_result from ask()."""
    session = MagicMock()
    session.ask.return_value = ask_result
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# cmd_ask tests
# ---------------------------------------------------------------------------


class TestCmdAskTextOutput:
    @patch("claudestream._cli.SyncSession")
    def test_prints_text(self, mock_cls, capsys):
        result = AskResult(text="Hello world", cost_usd=0.01, duration_ms=100.0)
        mock_cls.return_value = _mock_sync_session_with_ask(result)
        ret = cmd_ask(prompt="hi", model="sonnet", profile="test")
        assert ret is None
        assert capsys.readouterr().out.strip() == "Hello world"

    @patch("claudestream._cli.SyncSession")
    def test_json_output(self, mock_cls, capsys):
        result = AskResult(text="Hello", cost_usd=0.005, duration_ms=50.0)
        mock_cls.return_value = _mock_sync_session_with_ask(result)
        ret = cmd_ask(prompt="hi", model="sonnet", profile="test", json_output=True)
        assert ret is None
        import json
        output = json.loads(capsys.readouterr().out)
        assert output["text"] == "Hello"
        assert output["cost_usd"] == 0.005


class TestCmdAskErrorHandling:
    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error(self, mock_cls, capsys):
        mock_cls.side_effect = ClaudeStreamError("bad config")
        ret = cmd_ask(prompt="hi", model="sonnet", profile="test")
        assert ret == 1
        assert "error: bad config" in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_keyboard_interrupt(self, mock_cls, capsys):
        session = MagicMock()
        session.ask.side_effect = KeyboardInterrupt()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = ctx
        ret = cmd_ask(prompt="hi", model="sonnet", profile="test")
        assert ret == 1
        assert "Interrupted." in capsys.readouterr().err

    def test_no_prompt(self, capsys):
        ret = cmd_ask(model="sonnet", profile="test")
        assert ret == 1
        assert "prompt argument required" in capsys.readouterr().err


class TestCmdAskStdin:
    @patch("claudestream._cli.SyncSession")
    def test_stdin_reads_prompt(self, mock_cls, capsys):
        from io import StringIO
        result = AskResult(text="response")
        mock_cls.return_value = _mock_sync_session_with_ask(result)
        with patch("sys.stdin", StringIO("piped prompt\n")):
            ret = cmd_ask(stdin=True, model="sonnet", profile="test")
        assert ret is None
        mock_cls.return_value.__enter__.return_value.ask.assert_called_once_with("piped prompt")


# ---------------------------------------------------------------------------
# cmd_doctor tests
# ---------------------------------------------------------------------------


class TestCmdDoctor:
    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value="2.5.0")
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_all_ok(self, mock_find, mock_version, capsys):
        ret = cmd_doctor()
        assert ret == 0
        out = capsys.readouterr().out
        assert "[ok] Binary found: /usr/bin/claude" in out
        assert "[ok] Version: 2.5.0" in out

    @patch("claudestream._cli.find_binary", side_effect=FileNotFoundError("not found"))
    def test_binary_not_found(self, mock_find, capsys):
        ret = cmd_doctor()
        assert ret == 1
        out = capsys.readouterr().out
        assert "[FAIL] Binary not found" in out

    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value=None)
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_version_unknown(self, mock_find, mock_version, capsys):
        ret = cmd_doctor()
        assert ret == 1
        out = capsys.readouterr().out
        assert "[FAIL] Could not determine version" in out

    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value="1.0.0")
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_version_below_minimum(self, mock_find, mock_version, capsys):
        ret = cmd_doctor()
        assert ret == 1
        out = capsys.readouterr().out
        assert "WARNING: below minimum" in out

    @patch("claudewheel.profile.resolve_profile", return_value={"CLAUDE_CONFIG_DIR": "/test"})
    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value="2.5.0")
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_profile_resolution(self, mock_find, mock_version, mock_profile, capsys):
        ret = cmd_doctor(profile="test")
        assert ret == 0
        out = capsys.readouterr().out
        assert "[ok] Profile 'test'" in out

    @patch("claudewheel.profile.resolve_profile", side_effect=Exception("no such profile"))
    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value="2.5.0")
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_profile_error(self, mock_find, mock_version, mock_profile, capsys):
        ret = cmd_doctor(profile="badprofile")
        assert ret == 1
        out = capsys.readouterr().out
        assert "[FAIL] Profile 'badprofile'" in out


# ---------------------------------------------------------------------------
# cmd_config tests
# ---------------------------------------------------------------------------


class TestCmdConfig:
    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value="2.5.0")
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_shows_binary_and_version(self, mock_find, mock_version, capsys):
        ret = cmd_config()
        assert ret is None
        out = capsys.readouterr().out
        assert "Binary: /usr/bin/claude" in out
        assert "Version: 2.5.0" in out
        assert "Minimum version:" in out

    @patch("claudestream._cli.find_binary", side_effect=FileNotFoundError("not found"))
    def test_binary_not_found(self, mock_find, capsys):
        ret = cmd_config()
        assert ret is None
        out = capsys.readouterr().out
        assert "Binary: not found" in out

    @patch("claudewheel.profile.resolve_profile", return_value={"CLAUDE_CONFIG_DIR": "/test", "KEY": "val"})
    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value="2.5.0")
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_shows_profile(self, mock_find, mock_version, mock_profile, capsys):
        ret = cmd_config(profile="myprofile")
        assert ret is None
        out = capsys.readouterr().out
        assert "Profile: myprofile" in out
        assert "CLAUDE_CONFIG_DIR=/test" in out
        assert "KEY=val" in out

    @patch("claudestream._cli.check_version", new_callable=AsyncMock, return_value=None)
    @patch("claudestream._cli.find_binary", return_value="/usr/bin/claude")
    def test_version_unknown(self, mock_find, mock_version, capsys):
        ret = cmd_config()
        assert ret is None
        out = capsys.readouterr().out
        assert "Version: unknown" in out
