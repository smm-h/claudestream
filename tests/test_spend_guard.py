"""The default test lane cannot reach the real claude binary.

These tests are the standing proof behind the integration opt-in: they assert
that a plain ``pytest`` run neither collects the live tests nor leaves a usable
``claude`` on ``PATH``. If someone deletes the guard from ``conftest.py``, or
weakens the opt-in check, these fail.

The module is skipped when the live lane IS enabled -- there the guard is
deliberately absent.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from claudestream._process import find_binary
from tests.spend_guard import (
    GUARD_BINARY_ENV,
    GUARD_EXIT_CODE,
    GUARD_MESSAGE,
    INTEGRATION_ENV,
    SKIP_REASON,
    live_lane_enabled,
)

#: Every module whose tests drive the real binary. They must reach it the same
#: way production does -- by name, through ``PATH`` -- or the poisoned shim
#: cannot stand between them and a real installation.
INTEGRATION_MODULES = (
    "tests.test_integration",
    "tests.test_mcp_handshake_integration",
)

pytestmark = pytest.mark.skipif(
    live_lane_enabled(),
    reason=f"{INTEGRATION_ENV}=1: the live lane deliberately has no spend guard",
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _shim_path() -> str:
    shim = os.environ.get(GUARD_BINARY_ENV)
    assert shim, f"{GUARD_BINARY_ENV} is unset: the spend guard did not install"
    return shim


def test_which_claude_resolves_to_the_poisoned_shim():
    """PATH lookups find the guard, never a real installation."""
    assert shutil.which("claude") == _shim_path()


def test_find_binary_returns_the_poisoned_shim():
    """claudestream's own resolver goes through the same poisoned PATH."""
    assert find_binary() == _shim_path()


def test_the_poisoned_shim_refuses_to_run():
    """Spawning the resolved binary fails loudly instead of spending."""
    proc = subprocess.run(
        [_shim_path(), "--version"], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == GUARD_EXIT_CODE
    assert GUARD_MESSAGE in proc.stderr


@pytest.mark.parametrize("module_name", INTEGRATION_MODULES)
def test_integration_modules_resolve_the_binary_by_name(module_name):
    """No integration module may pin an absolute path to the real binary.

    An absolute path walks straight around the poisoned ``PATH``: the shim is
    only ever consulted for a *name* lookup. A module that hardcodes
    ``/somewhere/bin/claude`` opts its whole file out of the spend guard, and
    bakes a developer's home directory into a public repository besides.
    """
    binary = importlib.import_module(module_name).BINARY
    assert not os.path.isabs(binary), (
        f"{module_name}.BINARY is the absolute path {binary!r}; the poisoned "
        "PATH cannot intercept it. Name the binary instead."
    )
    assert os.sep not in binary, (
        f"{module_name}.BINARY is the path {binary!r}, not a bare name."
    )


@pytest.mark.parametrize("module_name", INTEGRATION_MODULES)
def test_integration_modules_resolve_to_the_poisoned_shim(module_name):
    """What those modules would spawn in the default lane is the guard itself."""
    binary = importlib.import_module(module_name).BINARY
    assert shutil.which(binary) == _shim_path()


def test_integration_tests_are_skipped_without_the_opt_in():
    """A default-lane pytest run reports the live tests as skipped, not run.

    Runs pytest in a subprocess so the assertion is about a real collection
    pass, not about this session's own state.
    """
    env = {k: v for k, v in os.environ.items() if k != INTEGRATION_ENV}
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_integration.py::TestSingleTurnSend::test_pong_response",
            "tests/test_mcp_handshake_integration.py",
            "-p",
            "no:cacheprovider",
            "-q",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "2 skipped" in proc.stdout, proc.stdout


def test_opt_in_requires_the_exact_value_one(monkeypatch):
    """Only ``=1`` enables the live lane -- no truthiness guessing."""
    for value in ("", "0", "true", "yes", "TRUE", "2"):
        monkeypatch.setenv(INTEGRATION_ENV, value)
        assert not live_lane_enabled(), f"{value!r} must not enable the live lane"
    monkeypatch.setenv(INTEGRATION_ENV, "1")
    assert live_lane_enabled()
