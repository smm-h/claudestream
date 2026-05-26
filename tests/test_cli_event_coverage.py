"""Tests for complete event type coverage in CLI commands."""

from unittest.mock import patch, MagicMock

from claudestream.events import ApiRetry, RateLimit, Thinking, ToolResult
from claudestream._cli import EventPrinter, cmd_stream, cmd_repl


def _make_rate_limit(status: str = "rate_limited") -> RateLimit:
    return RateLimit(type="rate_limit", status=status)


def _make_api_retry(attempt: int = 1, max_retries: int = 3, error: str = "overloaded") -> ApiRetry:
    return ApiRetry(
        type="api_retry",
        attempt=attempt,
        max_retries=max_retries,
        error=error,
    )


def _mock_sync_session(events):
    """Create a mock SyncSession that yields the given events."""
    session = MagicMock()
    session.send.return_value = events
    session.model_name = "test-model"
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# EventPrinter (used by cmd_send)
# ---------------------------------------------------------------------------


class TestEventPrinterRateLimit:
    def test_rate_limit_prints_to_stderr(self, capsys):
        """EventPrinter prints rate limit status to stderr."""
        p = EventPrinter(footer=False)
        p.print_event(_make_rate_limit("rate_limited"))

        captured = capsys.readouterr()
        assert "[rate limit: rate_limited]" in captured.err
        assert captured.out == ""


class TestEventPrinterFullOutput:
    """Tests that EventPrinter prints full content without truncation."""

    def test_tool_result_long_content_printed_in_full(self, capsys):
        """Long ToolResult content is printed without truncation."""
        long_content = "x" * 600
        event = ToolResult(type="tool_result", content=long_content)
        p = EventPrinter(footer=False)
        p.print_event(event)

        captured = capsys.readouterr()
        assert "x" * 600 in captured.out
        assert "..." not in captured.out

    def test_tool_result_short_content(self, capsys):
        """Short ToolResult content is printed in full."""
        short_content = "hello world"
        event = ToolResult(type="tool_result", content=short_content)
        p = EventPrinter(footer=False)
        p.print_event(event)

        captured = capsys.readouterr()
        assert "hello world" in captured.out
        assert "..." not in captured.out

    def test_thinking_long_text_printed_in_full(self, capsys):
        """Long thinking text is printed without truncation."""
        long_text = "t" * 200
        event = Thinking(type="thinking", text=long_text)
        p = EventPrinter(footer=False)
        p.print_event(event)

        captured = capsys.readouterr()
        assert "t" * 200 in captured.out
        assert "..." not in captured.out

    def test_thinking_short_text(self, capsys):
        """Short thinking text is printed in full."""
        short_text = "brief thought"
        event = Thinking(type="thinking", text=short_text)
        p = EventPrinter(footer=False)
        p.print_event(event)

        captured = capsys.readouterr()
        assert "brief thought" in captured.out
        assert "..." not in captured.out


# ---------------------------------------------------------------------------
# cmd_stream
# ---------------------------------------------------------------------------


class TestCmdStreamEventCoverage:
    @patch("claudestream._cli.SyncSession")
    def test_api_retry_prints_to_stderr(self, mock_cls, capsys):
        """cmd_stream prints retry info to stderr."""
        events = [_make_api_retry(attempt=2, max_retries=5, error="overloaded")]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "[retry 2/5: overloaded]" in captured.err

    @patch("claudestream._cli.SyncSession")
    def test_rate_limit_prints_to_stderr(self, mock_cls, capsys):
        """cmd_stream prints rate limit status to stderr."""
        events = [_make_rate_limit("rate_limited")]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "[rate limit: rate_limited]" in captured.err


# ---------------------------------------------------------------------------
# cmd_repl
# ---------------------------------------------------------------------------


class TestCmdReplEventCoverage:
    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=["hi", EOFError])
    def test_api_retry_prints_to_stderr(self, mock_input, mock_cls, capsys):
        """cmd_repl prints retry info to stderr."""
        events = [_make_api_retry(attempt=1, max_retries=3, error="server error")]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_repl(model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "[retry 1/3: server error]" in captured.err
