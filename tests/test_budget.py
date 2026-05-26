"""Tests for budget enforcement: max_cost_usd, max_turns, max_tokens."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream._options import Budget, SessionConfig
from claudestream._process import ProcessConfig
from claudestream.events import Result, Usage
from tests.conftest import make_test_session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_ndjson(events: list[dict]) -> bytes:
    return "".join(json.dumps(e) + "\n" for e in events).encode("utf-8")


def _wire_ndjson(session: AsyncSession, data: bytes) -> None:
    """Wire NDJSON data into a mocked process for the session."""
    session._process_mgr._process = MagicMock()
    session._process_mgr._process.returncode = None

    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    session._process_mgr._process.stdout = reader

    stdin_mock = MagicMock()
    stdin_mock.drain = AsyncMock()
    session._process_mgr._process.stdin = stdin_mock


# ---------------------------------------------------------------------------
# Raw event fixtures
# ---------------------------------------------------------------------------

_SYSTEM_INIT = {
    "type": "system",
    "subtype": "init",
    "cwd": "/test",
    "tools": [],
    "model": "test-model",
    "session_id": "s1",
}

_RESULT_WITH_USAGE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 100.0,
    "duration_api_ms": 80.0,
    "num_turns": 1,
    "result": "ok",
    "stop_reason": "end_turn",
    "total_cost_usd": 0.01,
    "session_id": "s1",
    "usage": {
        "input_tokens": 40,
        "output_tokens": 10,
    },
}

_RESULT_LARGE_USAGE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "duration_ms": 200.0,
    "duration_api_ms": 150.0,
    "num_turns": 1,
    "result": "ok",
    "stop_reason": "end_turn",
    "total_cost_usd": 0.05,
    "session_id": "s1",
    "usage": {
        "input_tokens": 60,
        "output_tokens": 40,
    },
}

_RESULT_NO_USAGE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "ok",
    "session_id": "s1",
}


# ---------------------------------------------------------------------------
# 5.1: max_cost_usd -> --max-budget-usd in argv
# ---------------------------------------------------------------------------


class TestMaxCostInArgv:
    def test_max_cost_in_argv(self):
        """Budget with max_cost_usd produces --max-budget-usd in argv."""
        session = make_test_session(
            budget=Budget(max_cost_usd=1.5),
        )
        argv = session._process_mgr.config.build_argv()
        idx = argv.index("--max-budget-usd")
        assert argv[idx + 1] == "1.5"

    def test_no_budget_no_max_budget_flag(self):
        """Without budget, --max-budget-usd is absent."""
        session = make_test_session()
        argv = session._process_mgr.config.build_argv()
        assert "--max-budget-usd" not in argv

    def test_budget_without_max_cost(self):
        """Budget with only max_turns (no max_cost_usd) omits --max-budget-usd."""
        session = make_test_session(
            budget=Budget(max_turns=5),
        )
        argv = session._process_mgr.config.build_argv()
        assert "--max-budget-usd" not in argv


# ---------------------------------------------------------------------------
# 5.2: Turn counter
# ---------------------------------------------------------------------------


class TestTurnCounterIncrements:
    def test_turn_counter_increments(self):
        """After 3 Result events, turn_count is 3."""

        async def run():
            session = make_test_session()
            for _ in range(3):
                data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
                _wire_ndjson(session, data)
                async for _ in session.send("hi"):
                    pass
            return session.turn_count

        count = asyncio.run(run())
        assert count == 3

    def test_turn_count_starts_at_zero(self):
        """turn_count is 0 before any sends."""
        session = make_test_session()
        assert session.turn_count == 0


class TestMaxTurnsEnforced:
    def test_max_turns_enforced(self):
        """With max_turns=2, third send() raises ClaudeStreamError."""

        async def run():
            session = make_test_session(
                budget=Budget(max_turns=2),
            )
            # Turn 1
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("turn 1"):
                pass
            assert session.turn_count == 1

            # Turn 2
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("turn 2"):
                pass
            assert session.turn_count == 2

            # Turn 3 should raise
            with pytest.raises(ClaudeStreamError, match="max_turns limit reached"):
                async for _ in session.send("turn 3"):
                    pass

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 5.3: Token accumulator
# ---------------------------------------------------------------------------


class TestTokenAccumulator:
    def test_total_tokens_accumulates(self):
        """Tokens from Result usage are accumulated across turns."""

        async def run():
            session = make_test_session()
            # Turn 1: 40 + 10 = 50
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_tokens == 50

            # Turn 2: 60 + 40 = 100, total = 150
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_LARGE_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_tokens == 150
            return session.total_tokens

        total = asyncio.run(run())
        assert total == 150

    def test_total_tokens_starts_at_zero(self):
        """total_tokens is 0 before any sends."""
        session = make_test_session()
        assert session.total_tokens == 0

    def test_no_usage_does_not_crash(self):
        """Result without usage field doesn't add to total_tokens."""

        async def run():
            session = make_test_session()
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_NO_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            return session.total_tokens

        total = asyncio.run(run())
        assert total == 0


class TestMaxTokensEnforced:
    def test_max_tokens_enforced(self):
        """With max_tokens=100, send() after exceeding raises."""

        async def run():
            session = make_test_session(
                budget=Budget(max_tokens=100),
            )
            # Turn 1: 40 + 10 = 50 tokens, under budget
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("turn 1"):
                pass
            assert session.total_tokens == 50

            # Turn 2: 60 + 40 = 100 tokens, total = 150, over budget
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_LARGE_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("turn 2"):
                pass
            assert session.total_tokens == 150

            # Turn 3 should raise
            with pytest.raises(ClaudeStreamError, match="max_tokens limit reached"):
                async for _ in session.send("turn 3"):
                    pass

        asyncio.run(run())


# ---------------------------------------------------------------------------
# No budget / partial budget
# ---------------------------------------------------------------------------


class TestNoBudgetNoLimit:
    def test_no_budget_unlimited_turns(self):
        """Without budget, unlimited turns work fine."""

        async def run():
            session = make_test_session()
            for i in range(5):
                data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
                _wire_ndjson(session, data)
                async for _ in session.send(f"turn {i}"):
                    pass
            return session.turn_count

        count = asyncio.run(run())
        assert count == 5


class TestBudgetWithNoneFields:
    def test_budget_max_turns_none(self):
        """Budget with max_turns=None doesn't enforce turns."""

        async def run():
            session = make_test_session(
                budget=Budget(max_turns=None, max_cost_usd=10.0),
            )
            for i in range(5):
                data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
                _wire_ndjson(session, data)
                async for _ in session.send(f"turn {i}"):
                    pass
            return session.turn_count

        count = asyncio.run(run())
        assert count == 5

    def test_budget_max_tokens_none(self):
        """Budget with max_tokens=None doesn't enforce tokens."""

        async def run():
            session = make_test_session(
                budget=Budget(max_tokens=None, max_cost_usd=10.0),
            )
            for i in range(5):
                data = _build_ndjson([_SYSTEM_INIT, _RESULT_LARGE_USAGE])
                _wire_ndjson(session, data)
                async for _ in session.send(f"turn {i}"):
                    pass
            return session.total_tokens

        total = asyncio.run(run())
        # 5 turns * (60 + 40) = 500
        assert total == 500
