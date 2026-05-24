"""Tests for --footer/--no-footer flag across all CLI commands."""

from unittest.mock import patch, MagicMock

from claudestream.events import AssistantText, StreamDelta, Result
from claudestream._cli import EventPrinter, cmd_stream, cmd_repl


def _make_stream_delta(text: str) -> StreamDelta:
    return StreamDelta(
        type="stream_delta",
        event={"delta": {"type": "text_delta", "text": text}},
    )


def _make_assistant_text(text: str) -> AssistantText:
    return AssistantText(type="assistant_text", text=text)


def _make_result(duration_ms: float = 100.0, cost: float = 0.001) -> Result:
    return Result(type="result", duration_ms=duration_ms, total_cost_usd=cost)


def _mock_sync_session(events):
    """Create a mock SyncSession that yields the given events."""
    session = MagicMock()
    session.send.return_value = events
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# EventPrinter (used by cmd_send)
# ---------------------------------------------------------------------------


class TestEventPrinterFooter:
    def test_footer_true_prints_to_stderr(self, capsys):
        """EventPrinter with footer=True prints Done line to stderr."""
        p = EventPrinter(footer=True)
        p.print_event(_make_stream_delta("hello"))
        p.print_event(_make_result(duration_ms=250.0, cost=0.0042))

        captured = capsys.readouterr()
        assert "--- Done (250ms, $0.0042) ---" in captured.err
        # Footer should NOT appear on stdout
        assert "Done" not in captured.out

    def test_footer_false_no_output(self, capsys):
        """EventPrinter with footer=False does not print Done line."""
        p = EventPrinter(footer=False)
        p.print_event(_make_stream_delta("hello"))
        p.print_event(_make_result(duration_ms=250.0, cost=0.0042))

        captured = capsys.readouterr()
        assert "Done" not in captured.err
        assert "Done" not in captured.out

    def test_footer_false_still_resets_buffer(self, capsys):
        """Buffer resets on Result even when footer is disabled."""
        p = EventPrinter(footer=False)
        p.print_event(_make_stream_delta("first"))
        p.print_event(_make_assistant_text("first"))
        p.print_event(_make_result())

        # After reset, new text should print
        p.print_event(_make_assistant_text("second"))

        captured = capsys.readouterr()
        assert "first" in captured.out
        assert "second" in captured.out
        assert captured.out.count("first") == 1


# ---------------------------------------------------------------------------
# cmd_stream
# ---------------------------------------------------------------------------


class TestCmdStreamFooter:
    @patch("claudestream._cli.SyncSession")
    def test_footer_true_prints_to_stderr(self, mock_cls, capsys):
        """cmd_stream with footer=True prints Done line to stderr."""
        events = [
            _make_stream_delta("hi"),
            _make_result(duration_ms=300.0, cost=0.005),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test", footer=True)

        captured = capsys.readouterr()
        assert "--- Done (300ms, $0.0050) ---" in captured.err
        assert "Done" not in captured.out

    @patch("claudestream._cli.SyncSession")
    def test_footer_false_no_done_line(self, mock_cls, capsys):
        """cmd_stream with footer=False does not print Done line."""
        events = [
            _make_stream_delta("hi"),
            _make_result(duration_ms=300.0, cost=0.005),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "Done" not in captured.err
        assert "Done" not in captured.out

    @patch("claudestream._cli.SyncSession")
    def test_footer_false_still_writes_newline(self, mock_cls, capsys):
        """cmd_stream always writes trailing newline to stdout on Result."""
        events = [
            _make_stream_delta("text"),
            _make_result(),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert captured.out == "text\n"


# ---------------------------------------------------------------------------
# cmd_repl
# ---------------------------------------------------------------------------


class TestCmdReplFooter:
    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=["hi", EOFError])
    def test_footer_true_prints_cost_to_stderr(self, mock_input, mock_cls, capsys):
        """cmd_repl with footer=True prints cost to stderr."""
        events = [
            _make_assistant_text("hey"),
            _make_result(cost=0.0023),
        ]
        session = MagicMock()
        session.send.return_value = events
        session.model_name = "test-model"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = ctx

        cmd_repl(model="sonnet", profile="test", footer=True)

        captured = capsys.readouterr()
        assert "[cost: $0.0023]" in captured.err
        assert "cost" not in captured.out

    @patch("claudestream._cli.SyncSession")
    @patch("builtins.input", side_effect=["hi", EOFError])
    def test_footer_false_no_cost(self, mock_input, mock_cls, capsys):
        """cmd_repl with footer=False does not print cost."""
        events = [
            _make_assistant_text("hey"),
            _make_result(cost=0.0023),
        ]
        session = MagicMock()
        session.send.return_value = events
        session.model_name = "test-model"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=session)
        ctx.__exit__ = MagicMock(return_value=False)
        mock_cls.return_value = ctx

        cmd_repl(model="sonnet", profile="test", footer=False)

        captured = capsys.readouterr()
        assert "cost" not in captured.err
        assert "cost" not in captured.out
