"""Tests for the event-based subprocess stuck detection.

These tests verify that the stuck detection correctly identifies subprocesses
that produce no events for longer than stuck_timeout, and correctly allows
subprocesses that produce events within the timeout window.
"""

import asyncio

import pytest

from unittest.mock import AsyncMock, MagicMock

from claudestream._async_session import ClaudeStreamError
from tests.conftest import make_test_session


@pytest.mark.timeout(15)
def test_stuck_detection_fires_after_silence():
    """When no events arrive for stuck_timeout seconds, raise 'Subprocess stuck'."""

    async def run():
        session = make_test_session()
        session._stuck_timeout = 0.3  # Short for testing

        # Create a reader that never sends data
        reader = asyncio.StreamReader()

        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = None
        session._process_mgr._process.pid = 12345
        session._process_mgr._process.stdout = reader
        session._process_mgr._process.stdin = MagicMock()
        session._process_mgr.close = AsyncMock()

        with pytest.raises(ClaudeStreamError, match="Subprocess stuck"):
            async for _ in session._read_turn(raw=True, _health_timeout=0.1):
                pass

        # The process should have been closed
        session._process_mgr.close.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.timeout(15)
def test_events_prevent_stuck_detection():
    """Events arriving before stuck_timeout should prevent stuck detection.

    Subprocess writes a SystemInit event, pauses (longer than health_timeout
    but shorter than stuck_timeout), then writes a Result event. The turn
    should complete normally.
    """
    import json

    async def run():
        session = make_test_session()
        session._stuck_timeout = 2.0  # Longer than the delay

        reader = asyncio.StreamReader()

        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = None
        session._process_mgr._process.pid = 12345
        session._process_mgr._process.stdout = reader
        session._process_mgr._process.stdin = MagicMock()
        session._process_mgr.close = AsyncMock()

        # Feed events with a delay in between (longer than health_timeout
        # but shorter than stuck_timeout)
        async def feed_events():
            init_event = json.dumps({
                "type": "system", "subtype": "init",
                "session_id": "test", "model": "test", "tools": [],
                "cwd": "/tmp", "mcp_servers": [], "permission_mode": "default",
            })
            reader.feed_data((init_event + "\n").encode())

            # Wait longer than health_timeout (0.2s) but shorter than stuck_timeout (2.0s)
            await asyncio.sleep(0.5)

            result_event = json.dumps({
                "type": "result", "subtype": "success",
                "is_error": False, "duration_ms": 100, "duration_api_ms": 50,
                "num_turns": 1, "result": "", "cost_usd": 0, "total_cost_usd": 0,
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                "session_id": "test",
            })
            reader.feed_data((result_event + "\n").encode())

        asyncio.get_event_loop().create_task(feed_events())

        events = []
        async for event in session._read_turn(raw=True, _health_timeout=0.2):
            events.append(event)

        # Should have gotten both events without stuck detection
        assert len(events) >= 1, f"Expected events but got: {events}"
        session._process_mgr.close.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.timeout(15)
def test_readline_timeout_does_not_kill_within_stuck_timeout():
    """The readline timeout (health_timeout) fires as the poll interval, but
    if we're still within stuck_timeout, the subprocess should NOT be killed.

    Verifies that multiple readline timeouts accumulate before triggering
    the stuck detection.
    """
    import json

    async def run():
        session = make_test_session()
        session._stuck_timeout = 0.5  # Will fire after ~5 readline timeouts

        reader = asyncio.StreamReader()

        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = None
        session._process_mgr._process.pid = 12345
        session._process_mgr._process.stdout = reader
        session._process_mgr._process.stdin = MagicMock()
        session._process_mgr.close = AsyncMock()

        # Feed a result event after 0.3s (within stuck_timeout of 0.5s,
        # but after several readline timeouts of 0.1s each)
        async def feed_late_event():
            await asyncio.sleep(0.3)
            result_event = json.dumps({
                "type": "result", "subtype": "success",
                "is_error": False, "duration_ms": 100, "duration_api_ms": 50,
                "num_turns": 1, "result": "", "cost_usd": 0, "total_cost_usd": 0,
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                "session_id": "test",
            })
            reader.feed_data((result_event + "\n").encode())

        asyncio.get_event_loop().create_task(feed_late_event())

        events = []
        async for event in session._read_turn(raw=True, _health_timeout=0.1):
            events.append(event)

        # Should complete normally despite multiple readline timeouts
        assert len(events) >= 1
        session._process_mgr.close.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.timeout(15)
def test_stuck_message_contains_elapsed_time():
    """The error message should include the elapsed silence duration."""

    async def run():
        session = make_test_session()
        session._stuck_timeout = 0.2

        reader = asyncio.StreamReader()

        session._process_mgr._process = MagicMock()
        session._process_mgr._process.returncode = None
        session._process_mgr._process.pid = 12345
        session._process_mgr._process.stdout = reader
        session._process_mgr._process.stdin = MagicMock()
        session._process_mgr.close = AsyncMock()

        with pytest.raises(ClaudeStreamError, match=r"Subprocess stuck: no events for \d+s"):
            async for _ in session._read_turn(raw=True, _health_timeout=0.1):
                pass

    asyncio.run(run())
