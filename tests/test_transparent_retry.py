"""Tests for transparent subprocess retry on stuck detection.

When the liveness probe detects a stuck subprocess, the session should
automatically restart it with --resume and continue yielding events
without raising to the consumer.
"""

import asyncio

import pytest

from unittest.mock import AsyncMock, MagicMock, patch

from claudestream._async_session import (
    ClaudeStreamError,
    _RECOVERY_MESSAGES,
)
from claudestream.events import AssistantText, Result
from claudestream.messages import UserMessage
from tests.conftest import make_test_session

# Patch target for resolve_profile -- needed whenever _build_process_config is called
_PROFILE_PATCH = patch("claudewheel.profile.resolve_profile", return_value={})


def _wire_fake_stdin(session):
    """Wire a fake process with stdin into the session so send() can write to it."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None
    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


class TestRetryRestartsOnStuckSubprocess:
    """Verify the session restarts on stuck detection and continues yielding events."""

    def test_retry_restarts_and_continues(self):
        """When _read_turn raises 'Subprocess stuck' on the first attempt,
        send() should restart the subprocess and yield events from the
        second attempt transparently."""

        async def run():
            session = make_test_session()
            session._session_id = "test-session-id"
            _wire_fake_stdin(session)

            restart_called = False
            call_count = 0

            async def mock_read_turn(*, raw, _health_timeout):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ClaudeStreamError(
                        "Subprocess stuck: alive but producing no output (0% CPU)"
                    )
                else:
                    yield AssistantText(type="assistant_text", text="recovered response")
                    yield Result(
                        type="result",
                        subtype="success",
                        is_error=False,
                        duration_ms=100,
                        duration_api_ms=50,
                        num_turns=1,
                        result="",
                        total_cost_usd=0.001,
                        usage=MagicMock(input_tokens=10, output_tokens=5),
                        session_id="test-session-id",
                    )

            async def mock_restart():
                nonlocal restart_called
                restart_called = True
                # Re-wire fake stdin after restart (simulates new process)
                _wire_fake_stdin(session)

            session._read_turn = mock_read_turn
            session._restart_subprocess = mock_restart

            events = []
            async for event in session.send("hello"):
                events.append(event)

            assert restart_called, "_restart_subprocess was not called"
            assert call_count == 2, f"Expected 2 _read_turn calls, got {call_count}"

            text_events = [e for e in events if isinstance(e, AssistantText)]
            assert len(text_events) == 1
            assert text_events[0].text == "recovered response"

            result_events = [e for e in events if isinstance(e, Result)]
            assert len(result_events) == 1

        asyncio.run(run())


class TestRetryMaxRetriesExceeded:
    """Verify that after max_retries, the error propagates to the consumer."""

    def test_error_raised_after_max_retries(self):
        """When the subprocess is stuck on every attempt, the error should
        be raised after max_retries (3) restarts."""

        async def run():
            session = make_test_session()
            session._session_id = "test-session-id"
            _wire_fake_stdin(session)

            call_count = 0
            restart_count = 0

            async def mock_read_turn(*, raw, _health_timeout):
                nonlocal call_count
                call_count += 1
                raise ClaudeStreamError(
                    "Subprocess stuck: alive but producing no output (0% CPU)"
                )
                yield  # pragma: no cover

            async def mock_restart():
                nonlocal restart_count
                restart_count += 1
                _wire_fake_stdin(session)

            session._read_turn = mock_read_turn
            session._restart_subprocess = mock_restart

            with pytest.raises(ClaudeStreamError, match="Subprocess stuck"):
                async for _ in session.send("hello"):
                    pass

            # Should have been called 4 times (initial + 3 retries)
            assert call_count == 4, f"Expected 4 _read_turn calls, got {call_count}"
            # Should have restarted 3 times (not on the last failure)
            assert restart_count == 3, f"Expected 3 restarts, got {restart_count}"

        asyncio.run(run())

    def test_non_stuck_error_not_retried(self):
        """Errors that are not 'Subprocess stuck' should propagate immediately."""

        async def run():
            session = make_test_session()
            session._session_id = "test-session-id"
            _wire_fake_stdin(session)

            async def mock_read_turn(*, raw, _health_timeout):
                raise ClaudeStreamError("Authentication failed")
                yield  # pragma: no cover

            async def mock_restart():
                raise AssertionError("Should not restart on non-stuck error")

            session._read_turn = mock_read_turn
            session._restart_subprocess = mock_restart

            with pytest.raises(ClaudeStreamError, match="Authentication failed"):
                async for _ in session.send("hello"):
                    pass

        asyncio.run(run())


class TestRecoveryMessage:
    """Verify the recovery message sent after restart is from the allowed list."""

    def test_recovery_message_is_from_list(self):
        """After restart, the recovery message sent via write_message should
        be one of the predefined _RECOVERY_MESSAGES."""

        async def run():
            session = make_test_session()
            session._session_id = "test-session-id"
            _wire_fake_stdin(session)

            call_count = 0
            captured_messages = []

            async def mock_read_turn(*, raw, _health_timeout):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise ClaudeStreamError(
                        "Subprocess stuck: alive but producing no output (0% CPU)"
                    )
                yield Result(
                    type="result",
                    subtype="success",
                    is_error=False,
                    duration_ms=100,
                    duration_api_ms=50,
                    num_turns=1,
                    result="",
                    total_cost_usd=0.001,
                    usage=MagicMock(input_tokens=10, output_tokens=5),
                    session_id="test-session-id",
                )

            async def mock_restart():
                _wire_fake_stdin(session)

            async def mock_write_message(stream, msg):
                if isinstance(msg, UserMessage):
                    captured_messages.append(msg)

            session._read_turn = mock_read_turn
            session._restart_subprocess = mock_restart

            with patch("claudestream._async_session.write_message", side_effect=mock_write_message):
                async for _ in session.send("hello"):
                    pass

            # First message is the original prompt, second is the recovery message
            assert len(captured_messages) == 2
            assert captured_messages[0].content == "hello"
            assert captured_messages[1].content in _RECOVERY_MESSAGES

        asyncio.run(run())

    def test_recovery_messages_list_not_empty(self):
        """Sanity check that the recovery messages list exists and is non-empty."""
        assert len(_RECOVERY_MESSAGES) > 0
        assert all(isinstance(m, str) for m in _RECOVERY_MESSAGES)


class TestRestartSubprocess:
    """Test the _restart_subprocess method itself."""

    def test_restart_sets_resume_override(self):
        """_restart_subprocess should set _resume_override to the current session_id."""

        async def run():
            session = make_test_session()
            session._session_id = "my-session-123"
            session._process_mgr.close = AsyncMock()

            with _PROFILE_PATCH, patch.object(session, "_start", new_callable=AsyncMock):
                await session._restart_subprocess()

            assert session._resume_override == "my-session-123"

        asyncio.run(run())

    def test_restart_increments_restart_count(self):
        """Each restart should increment _restart_count."""

        async def run():
            session = make_test_session()
            session._session_id = "sess-1"
            session._process_mgr.close = AsyncMock()

            with _PROFILE_PATCH, patch.object(session, "_start", new_callable=AsyncMock):
                assert session.restart_count == 0
                await session._restart_subprocess()
                assert session.restart_count == 1
                await session._restart_subprocess()
                assert session.restart_count == 2

        asyncio.run(run())

    def test_restart_resets_per_turn_state(self):
        """_restart_subprocess should reset per-turn state but keep session metadata."""

        async def run():
            session = make_test_session()
            session._session_id = "sess-1"
            session._model_name = "test-model"
            session._turn_count = 5
            session._total_tokens = 1000
            session._files_modified = {"a.py", "b.py"}
            session._startup_events = [MagicMock()]
            session._got_first_assistant = True
            session._active_turn = True
            session._cancelled = True
            session._process_mgr.close = AsyncMock()

            with _PROFILE_PATCH, patch.object(session, "_start", new_callable=AsyncMock):
                await session._restart_subprocess()

            # Per-turn state should be reset
            assert session._startup_events == []
            assert session._got_first_assistant is False
            assert session._active_turn is False
            assert session._cancelled is False

            # Session metadata should be preserved
            assert session._session_id == "sess-1"
            assert session._model_name == "test-model"
            assert session._turn_count == 5
            assert session._total_tokens == 1000
            assert session._files_modified == {"a.py", "b.py"}

        asyncio.run(run())

    def test_restart_rebuilds_process_manager(self):
        """After restart, the process manager should have the resume flag."""

        async def run():
            session = make_test_session()
            session._session_id = "sess-for-resume"
            old_pm = session._process_mgr
            session._process_mgr.close = AsyncMock()

            with _PROFILE_PATCH, patch.object(session, "_start", new_callable=AsyncMock):
                await session._restart_subprocess()

            new_pm = session._process_mgr
            assert new_pm is not old_pm
            assert new_pm.config.resume_session_id == "sess-for-resume"

        asyncio.run(run())


class TestResumeOverrideInProcessConfig:
    """Test that _resume_override is correctly used in _build_process_config."""

    def test_override_takes_precedence(self):
        """When _resume_override is set, it should be used instead of config.resume_session_id."""
        session = make_test_session(resume_session_id="original-id")
        session._resume_override = "override-id"
        with _PROFILE_PATCH:
            config = session._build_process_config()
        assert config.resume_session_id == "override-id"

    def test_no_override_uses_config(self):
        """When _resume_override is None, config.resume_session_id is used."""
        session = make_test_session(resume_session_id="original-id")
        with _PROFILE_PATCH:
            config = session._build_process_config()
        assert config.resume_session_id == "original-id"

    def test_no_override_no_config(self):
        """When both are None, resume_session_id should be None."""
        session = make_test_session()
        with _PROFILE_PATCH:
            config = session._build_process_config()
        assert config.resume_session_id is None


class TestRestartCountProperty:
    """Test the restart_count property."""

    def test_initial_value(self):
        session = make_test_session()
        assert session.restart_count == 0

    def test_exposed_as_property(self):
        session = make_test_session()
        session._restart_count = 5
        assert session.restart_count == 5


class TestRecoveryMessagesList:
    """Test the _RECOVERY_MESSAGES constant."""

    def test_all_strings(self):
        for msg in _RECOVERY_MESSAGES:
            assert isinstance(msg, str)

    def test_all_nonempty(self):
        for msg in _RECOVERY_MESSAGES:
            assert len(msg) > 0

    def test_at_least_three(self):
        """Enough variety for random selection."""
        assert len(_RECOVERY_MESSAGES) >= 3
