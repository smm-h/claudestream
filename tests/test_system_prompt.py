"""Tests for --system-prompt / -s flag across all CLI commands."""

from unittest.mock import patch, MagicMock

import pytest

from claudestream._cli import cmd_send, cmd_stream, cmd_events, cmd_repl


def _mock_sync_session():
    """Create a mock SyncSession context manager and return (mock_cls, session)."""
    session = MagicMock()
    session.send.return_value = []
    session.model_name = "test-model"
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestSystemPromptPassedThrough:
    """Verify that system_prompt is forwarded to SyncSession when provided."""

    @patch("claudestream._cli.SyncSession")
    def test_cmd_send_passes_system_prompt(self, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_send("hello", model="sonnet", profile="test", system_prompt="Be concise")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] == "Be concise"

    @patch("claudestream._cli.SyncSession")
    def test_cmd_stream_passes_system_prompt(self, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_stream("hello", model="sonnet", profile="test", system_prompt="Be concise")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] == "Be concise"

    @patch("claudestream._cli.SyncSession")
    def test_cmd_events_passes_system_prompt(self, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_events("hello", model="sonnet", profile="test", system_prompt="Be concise")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] == "Be concise"

    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=EOFError)
    def test_cmd_repl_passes_system_prompt(self, mock_input, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_repl(model="sonnet", profile="test", system_prompt="Be concise")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] == "Be concise"


class TestSystemPromptNoneWhenEmpty:
    """Verify that system_prompt is None when not provided (empty string converted)."""

    @patch("claudestream._cli.SyncSession")
    def test_cmd_send_default_is_none(self, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_send("hello", model="sonnet", profile="test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] is None

    @patch("claudestream._cli.SyncSession")
    def test_cmd_stream_default_is_none(self, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_stream("hello", model="sonnet", profile="test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] is None

    @patch("claudestream._cli.SyncSession")
    def test_cmd_events_default_is_none(self, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_events("hello", model="sonnet", profile="test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] is None

    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=EOFError)
    def test_cmd_repl_default_is_none(self, mock_input, mock_cls):
        mock_cls.return_value = _mock_sync_session()
        cmd_repl(model="sonnet", profile="test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["system_prompt"] is None
