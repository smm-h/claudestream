"""Tests for budget observation: thresholds, cost logging, token/cost replacement semantics."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claudestream._async_session import AsyncSession
from claudestream._options import Budget, SessionConfig, validate_budget
from claudestream.events import BudgetThreshold, Result, Usage
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
        "input_tokens": 100,
        "output_tokens": 50,
    },
}

_RESULT_NO_USAGE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "ok",
    "session_id": "s1",
}


def _make_result(total_cost_usd: float, input_tokens: int = 0, output_tokens: int = 0) -> dict:
    """Build a result dict with configurable cost and usage."""
    r = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 100.0,
        "duration_api_ms": 80.0,
        "num_turns": 1,
        "result": "ok",
        "stop_reason": "end_turn",
        "total_cost_usd": total_cost_usd,
        "session_id": "s1",
    }
    if input_tokens or output_tokens:
        r["usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
    return r


# ---------------------------------------------------------------------------
# Turn counter
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


# ---------------------------------------------------------------------------
# Token accumulator (REPLACEMENT semantics)
# ---------------------------------------------------------------------------


class TestTokenAccumulator:
    def test_total_tokens_replacement(self):
        """Tokens use REPLACEMENT: second Result's usage is cumulative, not additive."""

        async def run():
            session = make_test_session()
            # Turn 1: input=40, output=10 -> 50 total
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_WITH_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_tokens == 50

            # Turn 2: input=100, output=50 -> 150 total (CUMULATIVE, includes turn 1)
            # With replacement: total_tokens = 100 + 50 = 150 (NOT 50 + 150 = 200)
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
        """Result without usage field doesn't change total_tokens."""

        async def run():
            session = make_test_session()
            data = _build_ndjson([_SYSTEM_INIT, _RESULT_NO_USAGE])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            return session.total_tokens

        total = asyncio.run(run())
        assert total == 0


# ---------------------------------------------------------------------------
# No budget / unlimited
# ---------------------------------------------------------------------------


class TestNoBudgetNoLimit:
    def test_no_budget_unlimited_turns(self):
        """Without budget, unlimited turns work fine (no enforcement)."""

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


# ---------------------------------------------------------------------------
# Cost threshold fires once
# ---------------------------------------------------------------------------


class TestCostThresholdFiresOnce:
    def test_fires_on_first_cross_not_second(self):
        """BudgetThreshold fires after Result when threshold is first crossed, not again."""

        async def run():
            session = make_test_session(
                budget=Budget(cost_thresholds=[0.05]),
            )

            # Turn 1: total_cost_usd=0.10, crosses 0.05
            result1 = _make_result(total_cost_usd=0.10, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result1])
            _wire_ndjson(session, data)
            events1 = []
            async for event in session.send("hi"):
                events1.append(event)

            thresholds1 = [e for e in events1 if isinstance(e, BudgetThreshold)]
            assert len(thresholds1) == 1
            assert thresholds1[0].metric == "cost"
            assert thresholds1[0].threshold == 0.05
            assert thresholds1[0].current_value == 0.10

            # Verify BudgetThreshold comes after Result
            result_idx = next(i for i, e in enumerate(events1) if isinstance(e, Result))
            threshold_idx = next(i for i, e in enumerate(events1) if isinstance(e, BudgetThreshold))
            assert threshold_idx > result_idx

            # Turn 2: total_cost_usd=0.15, still past 0.05 but already fired
            result2 = _make_result(total_cost_usd=0.15, input_tokens=60, output_tokens=20)
            data = _build_ndjson([_SYSTEM_INIT, result2])
            _wire_ndjson(session, data)
            events2 = []
            async for event in session.send("hi again"):
                events2.append(event)

            thresholds2 = [e for e in events2 if isinstance(e, BudgetThreshold)]
            assert len(thresholds2) == 0

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Multiple thresholds
# ---------------------------------------------------------------------------


class TestMultipleThresholds:
    def test_all_crossed_thresholds_fire_in_order(self):
        """When a single Result crosses multiple thresholds, all fire sorted by value."""

        async def run():
            session = make_test_session(
                budget=Budget(cost_thresholds=[0.01, 0.05, 0.10]),
            )

            result = _make_result(total_cost_usd=0.12, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            events = []
            async for event in session.send("hi"):
                events.append(event)

            thresholds = [e for e in events if isinstance(e, BudgetThreshold)]
            assert len(thresholds) == 3
            assert thresholds[0].threshold == 0.01
            assert thresholds[1].threshold == 0.05
            assert thresholds[2].threshold == 0.10

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Duplicate thresholds
# ---------------------------------------------------------------------------


class TestDuplicateThresholds:
    def test_duplicate_thresholds_deduplicated(self):
        """Duplicate threshold values fire only once."""

        async def run():
            session = make_test_session(
                budget=Budget(cost_thresholds=[0.05, 0.05]),
            )

            result = _make_result(total_cost_usd=0.10, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            events = []
            async for event in session.send("hi"):
                events.append(event)

            thresholds = [e for e in events if isinstance(e, BudgetThreshold)]
            assert len(thresholds) == 1
            assert thresholds[0].threshold == 0.05

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Zero threshold
# ---------------------------------------------------------------------------


class TestZeroThreshold:
    def test_zero_threshold_fires(self):
        """A threshold of 0.0 fires when any cost is reported."""

        async def run():
            session = make_test_session(
                budget=Budget(cost_thresholds=[0.0]),
            )

            result = _make_result(total_cost_usd=0.01, input_tokens=10, output_tokens=5)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            events = []
            async for event in session.send("hi"):
                events.append(event)

            thresholds = [e for e in events if isinstance(e, BudgetThreshold)]
            assert len(thresholds) == 1
            assert thresholds[0].threshold == 0.0
            assert thresholds[0].current_value == 0.01

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Negative threshold validation
# ---------------------------------------------------------------------------


class TestNegativeThresholdValidation:
    def test_negative_cost_threshold_raises(self):
        """validate_budget raises ValueError on negative cost threshold."""
        budget = Budget(cost_thresholds=[-1.0])
        with pytest.raises(ValueError, match="negative"):
            validate_budget(budget)

    def test_negative_turn_threshold_raises(self):
        """validate_budget raises ValueError on negative turn threshold."""
        budget = Budget(turn_thresholds=[-1])
        with pytest.raises(ValueError, match="negative"):
            validate_budget(budget)

    def test_negative_token_threshold_raises(self):
        """validate_budget raises ValueError on negative token threshold."""
        budget = Budget(token_thresholds=[-100])
        with pytest.raises(ValueError, match="negative"):
            validate_budget(budget)


# ---------------------------------------------------------------------------
# Cost accumulator replacement
# ---------------------------------------------------------------------------


class TestCostAccumulatorReplacement:
    def test_cost_replaces_not_accumulates(self):
        """total_cost_usd uses REPLACEMENT: session value = latest Result's total_cost_usd."""

        async def run():
            session = make_test_session()

            # Turn 1: total_cost_usd=0.05
            result1 = _make_result(total_cost_usd=0.05, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result1])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_cost_usd == 0.05

            # Turn 2: total_cost_usd=0.12 (cumulative from API, NOT 0.05+0.12)
            result2 = _make_result(total_cost_usd=0.12, input_tokens=100, output_tokens=50)
            data = _build_ndjson([_SYSTEM_INIT, result2])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_cost_usd == 0.12  # NOT 0.17

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Token accumulator replacement
# ---------------------------------------------------------------------------


class TestTokenAccumulatorReplacement:
    def test_tokens_replace_not_accumulate(self):
        """total_tokens uses REPLACEMENT: second Result's usage already includes first turn."""

        async def run():
            session = make_test_session()

            # Turn 1: input=40, output=10 -> 50
            result1 = _make_result(total_cost_usd=0.01, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result1])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_tokens == 50

            # Turn 2: input=100, output=50 -> 150 (cumulative, includes turn 1)
            result2 = _make_result(total_cost_usd=0.05, input_tokens=100, output_tokens=50)
            data = _build_ndjson([_SYSTEM_INIT, result2])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass
            assert session.total_tokens == 150  # NOT 200

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Turn threshold
# ---------------------------------------------------------------------------


class TestTurnThreshold:
    def test_fires_after_reaching_turn_count(self):
        """Turn threshold fires after the specified turn count is reached."""

        async def run():
            session = make_test_session(
                budget=Budget(turn_thresholds=[2]),
            )

            # Turn 1: turn_count=1, threshold=2, not crossed
            result1 = _make_result(total_cost_usd=0.01, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result1])
            _wire_ndjson(session, data)
            events1 = []
            async for event in session.send("turn 1"):
                events1.append(event)

            thresholds1 = [e for e in events1 if isinstance(e, BudgetThreshold)]
            assert len(thresholds1) == 0

            # Turn 2: turn_count=2, crosses threshold
            result2 = _make_result(total_cost_usd=0.02, input_tokens=80, output_tokens=20)
            data = _build_ndjson([_SYSTEM_INIT, result2])
            _wire_ndjson(session, data)
            events2 = []
            async for event in session.send("turn 2"):
                events2.append(event)

            thresholds2 = [e for e in events2 if isinstance(e, BudgetThreshold)]
            assert len(thresholds2) == 1
            assert thresholds2[0].metric == "turns"
            assert thresholds2[0].threshold == 2
            assert thresholds2[0].current_value == 2.0

            # Turn 3: threshold already fired, no new event
            result3 = _make_result(total_cost_usd=0.03, input_tokens=120, output_tokens=30)
            data = _build_ndjson([_SYSTEM_INIT, result3])
            _wire_ndjson(session, data)
            events3 = []
            async for event in session.send("turn 3"):
                events3.append(event)

            thresholds3 = [e for e in events3 if isinstance(e, BudgetThreshold)]
            assert len(thresholds3) == 0

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Token threshold
# ---------------------------------------------------------------------------


class TestTokenThreshold:
    def test_fires_when_total_tokens_cross(self):
        """Token threshold fires when total_tokens crosses the threshold."""

        async def run():
            session = make_test_session(
                budget=Budget(token_thresholds=[100]),
            )

            # input=80, output=30 -> 110 total, crosses 100
            result = _make_result(total_cost_usd=0.01, input_tokens=80, output_tokens=30)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            events = []
            async for event in session.send("hi"):
                events.append(event)

            thresholds = [e for e in events if isinstance(e, BudgetThreshold)]
            assert len(thresholds) == 1
            assert thresholds[0].metric == "tokens"
            assert thresholds[0].threshold == 100
            assert thresholds[0].current_value == 110.0

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Mixed metric ordering
# ---------------------------------------------------------------------------


class TestMixedMetricOrdering:
    def test_cost_fires_before_turn(self):
        """When both cost and turn thresholds cross, cost fires first (fixed metric order)."""

        async def run():
            session = make_test_session(
                budget=Budget(cost_thresholds=[0.01], turn_thresholds=[1]),
            )

            result = _make_result(total_cost_usd=0.05, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            events = []
            async for event in session.send("hi"):
                events.append(event)

            thresholds = [e for e in events if isinstance(e, BudgetThreshold)]
            assert len(thresholds) == 2
            assert thresholds[0].metric == "cost"
            assert thresholds[1].metric == "turns"

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Cost log output
# ---------------------------------------------------------------------------


class TestCostLogOutput:
    def test_cost_log_written(self, tmp_path):
        """When cost_log_path is set, a JSONL file is written after each Result."""

        async def run():
            log_file = tmp_path / "cost.jsonl"
            session = make_test_session(
                cost_log_path=str(log_file),
            )

            result = _make_result(total_cost_usd=0.05, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass

            # Verify file exists and has valid JSONL
            assert log_file.exists()
            lines = log_file.read_text().strip().split("\n")
            assert len(lines) == 1

            record = json.loads(lines[0])
            assert record["session_id"] == "s1"
            assert record["model"] == "test-model"
            assert record["turn"] == 1
            assert record["total_cost_usd"] == 0.05
            assert "timestamp" in record
            assert record["input_tokens"] == 40
            assert record["output_tokens"] == 10
            assert record["stop_reason"] == "end_turn"
            assert record["duration_ms"] == 100.0
            assert record["duration_api_ms"] == 80.0

        asyncio.run(run())


class TestCostLogNotSet:
    def test_no_log_file_when_path_is_none(self, tmp_path):
        """When cost_log_path is None, no file is created."""

        async def run():
            session = make_test_session()  # cost_log_path defaults to None

            result = _make_result(total_cost_usd=0.05, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass

            # No cost log file should exist anywhere in tmp_path
            assert list(tmp_path.iterdir()) == []

        asyncio.run(run())


# ---------------------------------------------------------------------------
# BudgetThreshold callback
# ---------------------------------------------------------------------------


class TestBudgetThresholdCallback:
    def test_callback_fires_on_threshold(self):
        """Registered callback fires when BudgetThreshold event is emitted."""

        async def run():
            session = make_test_session(
                budget=Budget(cost_thresholds=[0.01]),
            )

            captured = []
            session.on(BudgetThreshold, lambda evt: captured.append(evt))

            result = _make_result(total_cost_usd=0.05, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result])
            _wire_ndjson(session, data)
            async for _ in session.send("hi"):
                pass

            assert len(captured) == 1
            assert captured[0].metric == "cost"
            assert captured[0].threshold == 0.01

        asyncio.run(run())


# ---------------------------------------------------------------------------
# Threshold refire on resume (fresh session)
# ---------------------------------------------------------------------------


class TestThresholdRefireOnResume:
    def test_fresh_session_refires_thresholds(self):
        """A new session with fresh _fired_thresholds fires again for the same threshold."""

        async def run():
            # First session
            session1 = make_test_session(
                budget=Budget(cost_thresholds=[0.05]),
            )

            result1 = _make_result(total_cost_usd=0.10, input_tokens=40, output_tokens=10)
            data = _build_ndjson([_SYSTEM_INIT, result1])
            _wire_ndjson(session1, data)
            events1 = []
            async for event in session1.send("hi"):
                events1.append(event)

            thresholds1 = [e for e in events1 if isinstance(e, BudgetThreshold)]
            assert len(thresholds1) == 1

            # Second session (simulating resume -- new session object, fresh state)
            session2 = make_test_session(
                budget=Budget(cost_thresholds=[0.05]),
            )

            result2 = _make_result(total_cost_usd=0.15, input_tokens=100, output_tokens=50)
            data = _build_ndjson([_SYSTEM_INIT, result2])
            _wire_ndjson(session2, data)
            events2 = []
            async for event in session2.send("hi"):
                events2.append(event)

            thresholds2 = [e for e in events2 if isinstance(e, BudgetThreshold)]
            assert len(thresholds2) == 1
            assert thresholds2[0].threshold == 0.05
            assert thresholds2[0].current_value == 0.15

        asyncio.run(run())
