"""The spend guard, armed for every pytest invocation rooted at this repository.

claudestream's integration tests drive the REAL Claude Code binary against a
real profile and bill a real account. The default lane must therefore be
spend-free *by construction*. Two independent mechanisms enforce that here, and
``tests/test_spend_guard.py`` pins both:

1. :func:`pytest_collection_modifyitems` skips every ``@pytest.mark.integration``
   test unless ``CLAUDESTREAM_INTEGRATION=1`` is set.
2. :func:`pytest_configure` prepends a poisoned ``claude`` shim to ``PATH`` in
   that same default lane, so nothing -- not the known tests, not code written
   tomorrow, not a subprocess two levels down -- can resolve a real binary by
   name.

**This file is at the repository root on purpose.** The hooks used to live in
``tests/conftest.py``, which pytest only loads when the invocation reaches into
``tests/``: ``pytest scripts/`` collected and ran with an unpoisoned ``PATH``.
pytest loads the rootdir conftest for every invocation rooted here, whatever
path is named, so the guard is now repo-scoped. :func:`pytest_report_header`
makes that visible in the terminal rather than merely asserted.

See ``tests/spend_guard.py`` for the mechanics.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.spend_guard import (
    ARMED_HEADER,
    DISARMED_HEADER,
    INTEGRATION_ENV,
    SKIP_REASON,
    install_poisoned_binary,
    live_lane_enabled,
    remove_poisoned_binary,
)

_POISONED_BINARY: Path | None = None


def pytest_configure(config):
    """Arm the spend guard for the default (opt-out-of-nothing) lane."""
    global _POISONED_BINARY
    if not live_lane_enabled():
        _POISONED_BINARY = install_poisoned_binary()


def pytest_report_header(config):
    """Report the guard's state, so a run never has to be trusted blindly."""
    if _POISONED_BINARY is None:
        return f"{DISARMED_HEADER} ({INTEGRATION_ENV}=1: this lane spends real money)"
    return f"{ARMED_HEADER} (poisoned claude shim at {_POISONED_BINARY})"


def pytest_unconfigure(config):
    """Disarm the spend guard, restoring ``PATH``."""
    global _POISONED_BINARY
    if _POISONED_BINARY is not None:
        remove_poisoned_binary(_POISONED_BINARY)
        _POISONED_BINARY = None


def pytest_collection_modifyitems(config, items):
    """Skip integration tests unless the live lane is explicitly opted into.

    Opt-in is the whole point: these tests drive the real Claude Code binary
    against a real profile and bill a real account, so running them has to be a
    deliberate act, never the consequence of a developer machine happening to
    have the prerequisites installed.
    """
    if live_lane_enabled():
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if item.get_closest_marker("integration") is not None:
            item.add_marker(skip)


def _missing_real_cli_prereqs(profile: str) -> str | None:
    """Return a skip reason if real-CLI prerequisites are absent, else None.

    Integration tests need two external prerequisites that are unavailable in
    credential-less environments (CI runners, fresh checkouts):

    - the ``claude`` binary on PATH, and
    - a resolvable claudewheel profile with usable launch env.

    claudewheel 0.22's ``resolve_profile`` raises (``ValueError`` for an unknown
    profile, ``TokenStoreError`` for corrupt tokens) instead of failing soft, so
    the resolution is wrapped broadly and any failure -- or an empty result --
    is treated as "prerequisites missing" and turns the test into a skip.
    """
    if shutil.which("claude") is None:
        return "claude CLI not found on PATH"
    try:
        from claudewheel.profile import resolve_profile

        env = resolve_profile(profile)
    except Exception as exc:  # noqa: BLE001 -- any resolution failure means skip
        return f"claudewheel profile {profile!r} unavailable: {exc}"
    if not env:
        return f"claudewheel profile {profile!r} resolved to empty env"
    return None


@pytest.fixture(autouse=True)
def _skip_without_real_cli(request):
    """Skip opted-in integration tests when the CLI/profile prerequisites fail.

    This is the second gate, not the first: collection has already skipped
    everything integration-marked unless the live lane was opted into. What is
    left is to turn a *broken* live environment (no binary, unresolvable
    profile) into a skip rather than an obscure failure.
    """
    if request.node.get_closest_marker("integration") is None:
        return
    profile = getattr(request.module, "PROFILE", "personal")
    reason = _missing_real_cli_prereqs(profile)
    if reason:
        pytest.skip(reason)
