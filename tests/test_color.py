"""Tests for ANSI color support, TTY auto-detection, and --color flag."""

from io import StringIO
from unittest.mock import patch, MagicMock

import pytest

from claudestream._color import Colorizer, should_color, _RESET, _RED, _YELLOW, _CYAN, _DIM, _BOLD
from claudestream._cli import EventPrinter, cmd_stream, app
from claudestream.events import (
    ApiRetry,
    RateLimit,
    Result,
    StreamDelta,
    Thinking,
    ToolUse,
)


# ---------------------------------------------------------------------------
# should_color()
# ---------------------------------------------------------------------------


class TestShouldColor:
    def test_returns_false_when_stream_is_not_tty(self):
        """Non-TTY streams (StringIO, pipes) should disable color."""
        stream = StringIO()
        assert should_color(stream=stream) is False

    def test_returns_false_when_color_flag_is_false(self):
        """The --no-color flag explicitly disables color."""
        tty = MagicMock()
        tty.isatty.return_value = True
        assert should_color(stream=tty, color_flag=False) is False

    def test_returns_false_when_no_color_env_var_is_set(self):
        """The NO_COLOR env var disables color (https://no-color.org/)."""
        tty = MagicMock()
        tty.isatty.return_value = True
        with patch.dict("os.environ", {"NO_COLOR": "1"}):
            assert should_color(stream=tty) is False

    def test_returns_true_for_tty_stream(self):
        """A TTY stream with no overrides enables color."""
        tty = MagicMock()
        tty.isatty.return_value = True
        with patch.dict("os.environ", {}, clear=True):
            assert should_color(stream=tty) is True

    def test_returns_false_when_stream_has_no_isatty(self):
        """Objects without isatty() are not TTYs."""
        stream = object()
        assert should_color(stream=stream) is False


# ---------------------------------------------------------------------------
# Colorizer
# ---------------------------------------------------------------------------


class TestColorizerEnabled:
    def test_red_wraps_with_ansi(self):
        c = Colorizer(use_color=True)
        assert c.red("error") == f"{_RED}error{_RESET}"

    def test_yellow_wraps_with_ansi(self):
        c = Colorizer(use_color=True)
        assert c.yellow("warn") == f"{_YELLOW}warn{_RESET}"

    def test_cyan_wraps_with_ansi(self):
        c = Colorizer(use_color=True)
        assert c.cyan("info") == f"{_CYAN}info{_RESET}"

    def test_dim_wraps_with_ansi(self):
        c = Colorizer(use_color=True)
        assert c.dim("faint") == f"{_DIM}faint{_RESET}"

    def test_bold_wraps_with_ansi(self):
        c = Colorizer(use_color=True)
        assert c.bold("strong") == f"{_BOLD}strong{_RESET}"


class TestColorizerDisabled:
    def test_red_returns_text_unchanged(self):
        c = Colorizer(use_color=False)
        assert c.red("error") == "error"

    def test_yellow_returns_text_unchanged(self):
        c = Colorizer(use_color=False)
        assert c.yellow("warn") == "warn"

    def test_cyan_returns_text_unchanged(self):
        c = Colorizer(use_color=False)
        assert c.cyan("info") == "info"

    def test_dim_returns_text_unchanged(self):
        c = Colorizer(use_color=False)
        assert c.dim("faint") == "faint"

    def test_bold_returns_text_unchanged(self):
        c = Colorizer(use_color=False)
        assert c.bold("strong") == "strong"


# ---------------------------------------------------------------------------
# EventPrinter with color
# ---------------------------------------------------------------------------


def _make_result(duration_ms: float = 100.0, cost: float = 0.001) -> Result:
    return Result(type="result", duration_ms=duration_ms, total_cost_usd=cost)


def _make_rate_limit(status: str = "rate_limited") -> RateLimit:
    return RateLimit(type="rate_limit", status=status)


def _make_api_retry(attempt: int = 1, max_retries: int = 3, error: str = "overloaded") -> ApiRetry:
    return ApiRetry(type="api_retry", attempt=attempt, max_retries=max_retries, error=error)


def _make_thinking(text: str = "Let me think about this...") -> Thinking:
    return Thinking(type="thinking", text=text)


class TestEventPrinterColor:
    def test_color_enabled_wraps_footer_with_cyan(self, capsys):
        """EventPrinter with color wraps the Done line in cyan."""
        c = Colorizer(use_color=True)
        p = EventPrinter(footer=True, color=c)
        p.print_event(_make_result(duration_ms=250.0, cost=0.0042))

        captured = capsys.readouterr()
        assert _CYAN in captured.err
        assert "--- Done (250ms, $0.0042) ---" in captured.err
        assert _RESET in captured.err

    def test_color_enabled_wraps_rate_limit_with_yellow(self, capsys):
        """EventPrinter with color wraps rate limit in yellow."""
        c = Colorizer(use_color=True)
        p = EventPrinter(footer=False, color=c)
        p.print_event(_make_rate_limit("rate_limited"))

        captured = capsys.readouterr()
        assert _YELLOW in captured.err
        assert "[rate limit: rate_limited]" in captured.err

    def test_color_enabled_wraps_retry_with_yellow(self, capsys):
        """EventPrinter with color wraps retry in yellow."""
        c = Colorizer(use_color=True)
        p = EventPrinter(footer=False, color=c)
        p.print_event(_make_api_retry(attempt=2, max_retries=5, error="overloaded"))

        captured = capsys.readouterr()
        assert _YELLOW in captured.err
        assert "[retry 2/5: overloaded]" in captured.err

    def test_color_enabled_wraps_thinking_with_dim(self, capsys):
        """EventPrinter with color wraps thinking in dim."""
        c = Colorizer(use_color=True)
        p = EventPrinter(footer=False, color=c)
        p.print_event(_make_thinking("pondering..."))

        captured = capsys.readouterr()
        assert _DIM in captured.out
        assert "thinking:" in captured.out

    def test_color_disabled_no_ansi_in_output(self, capsys):
        """EventPrinter without color produces no ANSI codes."""
        c = Colorizer(use_color=False)
        p = EventPrinter(footer=True, color=c)
        p.print_event(_make_result(duration_ms=100.0, cost=0.001))
        p.print_event(_make_rate_limit("limited"))
        p.print_event(_make_api_retry())

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "\033[" not in combined

    def test_default_color_is_disabled(self, capsys):
        """EventPrinter with no color argument defaults to no color."""
        p = EventPrinter(footer=True)
        p.print_event(_make_result(duration_ms=100.0, cost=0.001))

        captured = capsys.readouterr()
        assert "\033[" not in captured.err


# ---------------------------------------------------------------------------
# --color flag on commands
# ---------------------------------------------------------------------------


def _mock_sync_session(events):
    """Create a mock SyncSession that yields the given events."""
    session = MagicMock()
    session.send.return_value = events
    session.model_name = "test-model"
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=session)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestColorFlag:
    @patch("claudestream._cli.SyncSession")
    def test_color_false_suppresses_ansi_in_stream(self, mock_cls, capsys):
        """cmd_stream with color=False produces no ANSI codes on stderr."""
        events = [
            Thinking(type="thinking", text="hmm..."),
            RateLimit(type="rate_limit", status="rate_limited"),
            Result(type="result", duration_ms=100.0, total_cost_usd=0.001),
        ]
        mock_cls.return_value = _mock_sync_session(events)
        cmd_stream("hello", model="sonnet", profile="test", footer=True, color=False)

        captured = capsys.readouterr()
        assert "\033[" not in captured.err
        assert "[thinking...]" in captured.err
        assert "[rate limit: rate_limited]" in captured.err

    @patch("claudestream._cli.SyncSession")
    def test_no_color_flag_parsed_by_strictcli(self, mock_cls):
        """Verify strictcli recognizes --no-color on each command."""
        mock_cls.return_value = _mock_sync_session([])
        for command in ["send", "stream", "events"]:
            result = app.test([command, "--no-color", "--model", "opus", "--profile", "test", "hello"])
            assert "unknown" not in result.stderr.lower(), f"--no-color not recognized on {command}"

    def test_no_color_flag_parsed_on_repl(self):
        """Verify strictcli recognizes --no-color on the repl command."""
        result = app.test(["repl", "--no-color", "--model", "opus", "--profile", "test", "--help"])
        assert "unknown" not in result.stderr.lower()
