"""Tests for model name display in the repl command."""

from unittest.mock import patch, MagicMock

from claudestream.events import AssistantText, Result
from claudestream._cli import cmd_repl


def _make_assistant_text(text: str) -> AssistantText:
    return AssistantText(type="assistant_text", text=text)


def _make_result(cost: float = 0.001) -> Result:
    return Result(type="result", duration_ms=100.0, total_cost_usd=cost)


def _mock_session(model_name: str | None, events: list):
    """Create a mock SyncSession context manager."""
    session = MagicMock()
    session.send.return_value = events
    session.model_name = model_name
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestModelDisplay:
    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=["hello", EOFError])
    def test_connected_appears_on_stderr_after_first_response(
        self, mock_input, mock_cls, capsys
    ):
        """After first turn, 'Connected: <model>' appears on stderr."""
        events = [_make_assistant_text("hi"), _make_result()]
        mock_cls.return_value = _mock_session("claude-sonnet-4-20250514", events)

        cmd_repl(model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "Connected: claude-sonnet-4-20250514" in captured.err

    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=["hello", "again", EOFError])
    def test_connected_does_not_repeat_on_second_turn(
        self, mock_input, mock_cls, capsys
    ):
        """'Connected:' only appears once, not on the second turn."""
        events = [_make_assistant_text("hi"), _make_result()]
        mock_cls.return_value = _mock_session("claude-sonnet-4-20250514", events)

        cmd_repl(model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert captured.err.count("Connected:") == 1

    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=["hello", EOFError])
    def test_no_connected_when_model_name_empty(
        self, mock_input, mock_cls, capsys
    ):
        """If model_name remains None, no 'Connected:' line is printed."""
        events = [_make_assistant_text("hi"), _make_result()]
        mock_cls.return_value = _mock_session(None, events)

        cmd_repl(model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "Connected:" not in captured.err
