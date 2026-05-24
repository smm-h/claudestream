"""Integration tests that run against the real claude CLI with authentication.

These tests use the haiku model (cheapest/fastest) and the 'personal' profile.
Run with: uv run pytest tests/test_integration.py -v --timeout=60
Skip with: pytest -m "not integration"
"""

import pytest

from claudestream import (
    AssistantText,
    Result,
    StreamDelta,
    SyncSession,
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
