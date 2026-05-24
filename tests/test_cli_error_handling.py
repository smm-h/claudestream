"""Tests for exception handling in send, stream, and events CLI commands."""

from unittest.mock import patch, MagicMock

from claudestream import ClaudeStreamError
from claudestream._cli import cmd_send, cmd_stream, cmd_events


def _mock_sync_session(side_effect):
    """Create a mock SyncSession context manager that raises on send()."""
    session = MagicMock()
    session.send.side_effect = side_effect
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestCmdSendErrorHandling:
    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error(self, mock_cls, capsys):
        mock_cls.return_value = _mock_sync_session(ClaudeStreamError("session failed"))
        result = cmd_send("hello")
        assert result == 1
        assert "error: session failed" in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_keyboard_interrupt(self, mock_cls, capsys):
        mock_cls.return_value = _mock_sync_session(KeyboardInterrupt())
        result = cmd_send("hello")
        assert result == 1
        assert "Interrupted." in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error_on_init(self, mock_cls, capsys):
        """ClaudeStreamError raised during SyncSession() construction."""
        mock_cls.side_effect = ClaudeStreamError("bad config")
        result = cmd_send("hello")
        assert result == 1
        assert "error: bad config" in capsys.readouterr().err


class TestCmdStreamErrorHandling:
    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error(self, mock_cls, capsys):
        mock_cls.return_value = _mock_sync_session(ClaudeStreamError("connection lost"))
        result = cmd_stream("hello")
        assert result == 1
        assert "error: connection lost" in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_keyboard_interrupt(self, mock_cls, capsys):
        mock_cls.return_value = _mock_sync_session(KeyboardInterrupt())
        result = cmd_stream("hello")
        assert result == 1
        assert "Interrupted." in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error_on_init(self, mock_cls, capsys):
        mock_cls.side_effect = ClaudeStreamError("binary not found")
        result = cmd_stream("hello")
        assert result == 1
        assert "error: binary not found" in capsys.readouterr().err


class TestCmdEventsErrorHandling:
    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error(self, mock_cls, capsys):
        mock_cls.return_value = _mock_sync_session(ClaudeStreamError("parse error"))
        result = cmd_events("hello")
        assert result == 1
        assert "error: parse error" in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_keyboard_interrupt(self, mock_cls, capsys):
        mock_cls.return_value = _mock_sync_session(KeyboardInterrupt())
        result = cmd_events("hello")
        assert result == 1
        assert "Interrupted." in capsys.readouterr().err

    @patch("claudestream._cli.SyncSession")
    def test_claude_stream_error_on_init(self, mock_cls, capsys):
        mock_cls.side_effect = ClaudeStreamError("auth failed")
        result = cmd_events("hello")
        assert result == 1
        assert "error: auth failed" in capsys.readouterr().err
