"""Tests for buffer-based AssistantText deduplication."""

from unittest.mock import patch, MagicMock

from claudestream.events import AssistantText, StreamDelta, Result
from claudestream._cli import EventPrinter, cmd_stream


def _make_stream_delta(text: str) -> StreamDelta:
    """Create a StreamDelta event with the given text."""
    return StreamDelta(
        type="stream_delta",
        event={"delta": {"type": "text_delta", "text": text}},
    )


def _make_assistant_text(text: str) -> AssistantText:
    return AssistantText(type="assistant_text", text=text)


def _make_result() -> Result:
    return Result(type="result", duration_ms=100.0, total_cost_usd=0.001)


# ---------------------------------------------------------------------------
# EventPrinter (used by cmd_send)
# ---------------------------------------------------------------------------


class TestEventPrinterDedup:
    def test_stream_delta_then_identical_assistant_text_no_dup(self, capsys):
        """StreamDelta followed by identical AssistantText: only prints once."""
        p = EventPrinter()
        p.print_event(_make_stream_delta("Hello world"))
        p.print_event(_make_assistant_text("Hello world"))

        out = capsys.readouterr().out
        assert out == "Hello world"

    def test_assistant_text_no_prior_stream_delta_prints(self, capsys):
        """AssistantText with no prior StreamDelta prints the text (error case)."""
        p = EventPrinter()
        p.print_event(_make_assistant_text("auth failed: invalid token"))

        out = capsys.readouterr().out
        assert out == "auth failed: invalid token"

    def test_stream_delta_then_different_assistant_text_prints(self, capsys):
        """StreamDelta with different text from AssistantText: both print."""
        p = EventPrinter()
        p.print_event(_make_stream_delta("partial"))
        p.print_event(_make_assistant_text("completely different"))

        out = capsys.readouterr().out
        assert out == "partialcompletely different"

    def test_buffer_resets_after_result(self, capsys):
        """Buffer resets on Result so second turn works correctly."""
        p = EventPrinter()

        # Turn 1: stream delta, then identical assistant text (deduped)
        p.print_event(_make_stream_delta("first"))
        p.print_event(_make_assistant_text("first"))
        p.print_event(_make_result())

        # Turn 2: no stream delta, assistant text should print
        p.print_event(_make_assistant_text("second"))

        out = capsys.readouterr().out
        # "first" from StreamDelta + "\n--- Done ..." from Result + "second" from AssistantText
        assert "first" in out
        assert "second" in out
        # "first" should appear only once (deduped)
        assert out.count("first") == 1

    def test_multiple_stream_deltas_then_combined_assistant_text(self, capsys):
        """Multiple StreamDelta chunks followed by their combined AssistantText."""
        p = EventPrinter()
        p.print_event(_make_stream_delta("Hello "))
        p.print_event(_make_stream_delta("world"))
        p.print_event(_make_assistant_text("Hello world"))

        out = capsys.readouterr().out
        assert out == "Hello world"


# ---------------------------------------------------------------------------
# cmd_stream
# ---------------------------------------------------------------------------


def _mock_sync_session(events):
    """Create a mock SyncSession that yields the given events."""
    session = MagicMock()
    session.send.return_value = events
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestCmdStreamDedup:
    @patch("claudestream._cli.SyncSession")
    def test_stream_delta_then_identical_assistant_text(self, mock_cls, capsys):
        """cmd_stream: identical AssistantText is suppressed."""
        events = [
            _make_stream_delta("Hi there"),
            _make_assistant_text("Hi there"),
            _make_result(),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test")

        out = capsys.readouterr().out
        # "Hi there" from StreamDelta + "\n" from Result
        assert out == "Hi there\n"

    @patch("claudestream._cli.SyncSession")
    def test_no_stream_delta_assistant_text_prints(self, mock_cls, capsys):
        """cmd_stream: AssistantText with no StreamDelta prints the text."""
        events = [
            _make_assistant_text("error: unauthorized"),
            _make_result(),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test")

        out = capsys.readouterr().out
        assert "error: unauthorized" in out

    @patch("claudestream._cli.SyncSession")
    def test_different_text_prints_both(self, mock_cls, capsys):
        """cmd_stream: different AssistantText is printed."""
        events = [
            _make_stream_delta("streamed"),
            _make_assistant_text("different text"),
            _make_result(),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test")

        out = capsys.readouterr().out
        assert "streamed" in out
        assert "different text" in out

    @patch("claudestream._cli.SyncSession")
    def test_buffer_resets_after_result(self, mock_cls, capsys):
        """cmd_stream: buffer resets so second turn dedup works."""
        events = [
            # Turn 1
            _make_stream_delta("turn1"),
            _make_assistant_text("turn1"),
            _make_result(),
            # Turn 2: no stream delta, assistant text should print
            _make_assistant_text("turn2"),
            _make_result(),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test")

        out = capsys.readouterr().out
        assert "turn1" in out
        assert "turn2" in out
        assert out.count("turn1") == 1
