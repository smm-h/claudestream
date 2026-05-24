"""Integration tests that run against the real claude CLI with authentication.

These tests use the haiku model (cheapest/fastest) and the 'personal' profile.
Run with: uv run pytest tests/test_integration.py -v --timeout=60
Skip with: pytest -m "not integration"
"""

import subprocess
import threading

import pytest

from claudestream import (
    AssistantText,
    ClaudeStreamError,
    Result,
    StreamDelta,
    SyncSession,
    SystemInit,
    allow_all,
)

pytestmark = pytest.mark.integration

BINARY = "/home/m/.local/bin/claude"
MODEL = "haiku"
PROFILE = "personal"


def _make_session() -> SyncSession:
    return SyncSession(
        model=MODEL,
        profile=PROFILE,
        binary=BINARY,
        policy=allow_all(),
    )


class TestSingleTurnSend:
    """Send a single prompt and verify the response."""

    @pytest.mark.timeout(60)
    def test_pong_response(self):
        with _make_session() as session:
            events = list(session.send("respond with exactly the word 'pong'"))

            # Collect all text from AssistantText events
            text_parts = [e.text for e in events if isinstance(e, AssistantText)]
            full_text = "".join(text_parts)

            assert "pong" in full_text.lower(), f"Expected 'pong' in response, got: {full_text!r}"

            # Verify a Result event with non-zero cost
            results = [e for e in events if isinstance(e, Result)]
            assert len(results) == 1, f"Expected exactly 1 Result event, got {len(results)}"
            assert results[0].total_cost_usd > 0, "Expected non-zero cost"


class TestStreaming:
    """Verify streaming events arrive correctly."""

    @pytest.mark.timeout(60)
    def test_stream_deltas_and_assistant_text(self):
        with _make_session() as session:
            deltas = []
            texts = []
            result = None

            for event in session.send("respond with exactly 'hello world'"):
                if isinstance(event, StreamDelta):
                    deltas.append(event)
                elif isinstance(event, AssistantText):
                    texts.append(event)
                elif isinstance(event, Result):
                    result = event

            # At least one StreamDelta with non-empty text
            text_deltas = [d for d in deltas if d.text]
            assert len(text_deltas) > 0, "Expected at least one StreamDelta with text"

            # AssistantText events arrive
            assert len(texts) > 0, "Expected at least one AssistantText event"

            # Final text contains "hello world"
            full_text = "".join(t.text for t in texts)
            assert "hello world" in full_text.lower(), (
                f"Expected 'hello world' in response, got: {full_text!r}"
            )

            assert result is not None, "Expected a Result event"


class TestMultiTurnREPL:
    """Test multi-turn conversation with memory."""

    @pytest.mark.timeout(90)
    def test_remembers_number(self):
        with _make_session() as session:
            # First turn: ask to remember a number
            for _ in session.send("remember the number 42"):
                pass

            # Second turn: ask what number was remembered
            events = list(session.send("what number did I ask you to remember?"))
            text_parts = [e.text for e in events if isinstance(e, AssistantText)]
            full_text = "".join(text_parts)

            assert "42" in full_text, f"Expected '42' in response, got: {full_text!r}"


class TestFooterMetadata:
    """Verify Result event metadata."""

    @pytest.mark.timeout(60)
    def test_duration_and_cost(self):
        with _make_session() as session:
            events = list(session.send("say 'hi'"))

            results = [e for e in events if isinstance(e, Result)]
            assert len(results) == 1

            result = results[0]
            assert result.duration_ms > 0, f"Expected duration_ms > 0, got {result.duration_ms}"
            assert result.total_cost_usd > 0, (
                f"Expected total_cost_usd > 0, got {result.total_cost_usd}"
            )


class TestModelNameFromSystemInit:
    """Verify session.model_name is populated after first send."""

    @pytest.mark.timeout(60)
    def test_model_name_populated(self):
        with _make_session() as session:
            # Send a minimal prompt to trigger SystemInit
            for _ in session.send("say 'ok'"):
                pass

            assert session.model_name is not None, "model_name should not be None"
            assert len(session.model_name) > 0, "model_name should be non-empty"


class TestSystemPrompt:
    """Verify system prompt flows through and influences the response."""

    @pytest.mark.timeout(60)
    def test_system_prompt_influences_response(self):
        # Use --append-system-prompt via extra_args rather than the
        # system_prompt parameter, because the latter adds --bare which
        # blocks OAuth authentication.
        session = SyncSession(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            policy=allow_all(),
            extra_args=[
                "--append-system-prompt",
                "Always respond with exactly the word BANANA and nothing else.",
            ],
        )
        with session:
            events = list(session.send("hello"))
            text_parts = [e.text for e in events if isinstance(e, AssistantText)]
            full_text = "".join(text_parts)

            assert "banana" in full_text.lower(), (
                f"Expected 'banana' in response (system prompt should force it), got: {full_text!r}"
            )


class TestStdinPiping:
    """Verify --stdin reads the prompt from stdin via subprocess."""

    @pytest.mark.timeout(60)
    def test_stdin_flag_reads_from_pipe(self):
        result = subprocess.run(
            [
                "claudestream", "send", "--stdin",
                "--model", MODEL,
                "--profile", PROFILE,
                "--skip-permissions",
                "--no-color",
            ],
            input="respond with exactly pong",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Process exited with {result.returncode}, stderr: {result.stderr!r}"
        )
        assert "pong" in result.stdout.lower(), (
            f"Expected 'pong' in stdout, got: {result.stdout!r}"
        )


class TestSessionCancel:
    """Verify session.cancel() interrupts a running turn."""

    @pytest.mark.timeout(60)
    def test_cancel_raises_error(self):
        with _make_session() as session:
            events_before_cancel: list = []
            error_raised = False

            def cancel_after_delay():
                """Wait briefly then cancel the session."""
                # Give time for at least one event to arrive
                import time
                time.sleep(2)
                session.cancel()

            cancel_thread = threading.Thread(target=cancel_after_delay, daemon=True)
            cancel_thread.start()

            try:
                for event in session.send("Write a 500 word essay about clouds"):
                    events_before_cancel.append(event)
            except ClaudeStreamError as e:
                error_raised = True
                assert "cancel" in str(e).lower(), (
                    f"Expected cancel-related error, got: {e}"
                )

            cancel_thread.join(timeout=5)

            assert error_raised, (
                "Expected ClaudeStreamError from cancel(), but no error was raised. "
                f"Got {len(events_before_cancel)} events instead."
            )


class TestSystemInitVisible:
    """Verify SystemInit events are yielded in the event stream."""

    @pytest.mark.timeout(60)
    def test_system_init_in_events(self):
        with _make_session() as session:
            events = list(session.send("say 'hi'"))

            system_inits = [e for e in events if isinstance(e, SystemInit)]
            assert len(system_inits) >= 1, (
                f"Expected at least 1 SystemInit event, got {len(system_inits)}. "
                f"Event types: {[type(e).__name__ for e in events]}"
            )

            init = system_inits[0]
            assert init.model, "SystemInit.model should be non-empty"
            assert init.session_id, "SystemInit.session_id should be non-empty"


class TestAllEventTypes:
    """Verify minimum expected event types appear in a simple prompt."""

    @pytest.mark.timeout(60)
    def test_minimum_event_types_present(self):
        with _make_session() as session:
            events = list(session.send("say 'hello'"))

            type_names = {type(e).__name__ for e in events}

            assert "SystemInit" in type_names, (
                f"Expected SystemInit in events, got: {sorted(type_names)}"
            )
            assert "Result" in type_names, (
                f"Expected Result in events, got: {sorted(type_names)}"
            )
            # At least one text-bearing event type
            has_text = "AssistantText" in type_names or "StreamDelta" in type_names
            assert has_text, (
                f"Expected AssistantText or StreamDelta in events, got: {sorted(type_names)}"
            )


class TestColorDisabledInNonTTY:
    """Verify no ANSI escape codes appear when stderr is not a TTY."""

    @pytest.mark.timeout(60)
    def test_no_ansi_in_subprocess_stderr(self):
        result = subprocess.run(
            [
                "claudestream", "send", "say hi",
                "--model", MODEL,
                "--profile", PROFILE,
                "--skip-permissions",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Process exited with {result.returncode}, stderr: {result.stderr!r}"
        )
        # ANSI escape codes start with ESC[ (\033[)
        assert "\033[" not in result.stderr, (
            f"Found ANSI escape codes in stderr (non-TTY context): {result.stderr!r}"
        )
        assert "\033[" not in result.stdout, (
            f"Found ANSI escape codes in stdout (non-TTY context): {result.stdout!r}"
        )
