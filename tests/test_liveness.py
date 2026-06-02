"""Integration tests for the subprocess liveness probe.

These tests spawn real subprocesses and verify that the liveness probe
correctly detects idle vs busy processes.
"""

import asyncio

import pytest

from claudestream._async_session import ClaudeStreamError
from tests.conftest import make_test_session


@pytest.mark.slow
@pytest.mark.timeout(30)
def test_liveness_detects_idle_process():
    """Start a subprocess that writes one JSON line then goes idle.

    The liveness probe should detect 0% CPU and kill it.
    """

    async def run():
        session = make_test_session()

        # Spawn a real subprocess: writes one line then sleeps forever
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c",
            "import sys, time; sys.stdout.write('{}\\n'); sys.stdout.flush(); time.sleep(999)",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wire the real subprocess into the session's process manager
        session._process_mgr._process = proc

        # Use a short health timeout so the test doesn't take 60s
        with pytest.raises(ClaudeStreamError, match="Subprocess stuck"):
            async for _ in session._read_turn(raw=True, _health_timeout=2.0):
                pass

        # The process should have been killed
        assert proc.returncode is not None or not _is_alive(proc)

    asyncio.run(run())


@pytest.mark.slow
@pytest.mark.timeout(30)
def test_liveness_allows_busy_process():
    """Start a subprocess that writes one line, does CPU work, then writes another.

    The liveness probe should NOT kill it because CPU > 0%.
    """

    async def run():
        session = make_test_session()

        # Subprocess: writes a JSON line, does CPU work for 8 seconds,
        # then writes another JSON line with a result event, then exits.
        script = (
            "import sys, time, json\n"
            "sys.stdout.write(json.dumps({'type': 'system', 'subtype': 'init', "
            "'session_id': 'test', 'model': 'test', 'tools': [], "
            "'cwd': '/tmp', 'mcp_servers': [], 'permission_mode': 'default'}) + '\\n')\n"
            "sys.stdout.flush()\n"
            "# CPU-bound work for ~8 seconds\n"
            "end = time.time() + 8\n"
            "while time.time() < end:\n"
            "    x = sum(range(100000))\n"
            "# Write a result event to end the turn\n"
            "sys.stdout.write(json.dumps({'type': 'result', 'subtype': 'success', "
            "'is_error': False, 'duration_ms': 100, 'duration_api_ms': 50, "
            "'num_turns': 1, 'result': '', 'cost_usd': 0, 'total_cost_usd': 0, "
            "'usage': {'input_tokens': 10, 'output_tokens': 5, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 0}, "
            "'session_id': 'test'}) + '\\n')\n"
            "sys.stdout.flush()\n"
        )

        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", script,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        session._process_mgr._process = proc

        # Use a short health timeout so the liveness probe fires while
        # the subprocess is doing CPU work
        events = []
        async for event in session._read_turn(raw=True, _health_timeout=3.0):
            events.append(event)

        # The turn should complete normally with a Result event
        assert any(hasattr(e, "duration_ms") for e in events), (
            f"Expected a Result event but got: {[type(e).__name__ for e in events]}"
        )

        # Process should have exited cleanly
        await proc.wait()
        assert proc.returncode == 0

    asyncio.run(run())


def _is_alive(proc: asyncio.subprocess.Process) -> bool:
    """Check if a subprocess is still alive."""
    return proc.returncode is None
