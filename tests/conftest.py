"""Shared test fixtures for claudestream, and the spend guard that arms the suite.

The default ``pytest`` run must not be able to spend money. Two mechanisms in
this file enforce that, and ``tests/test_spend_guard.py`` pins both:

1. ``pytest_collection_modifyitems`` skips every ``@pytest.mark.integration``
   test unless ``CLAUDESTREAM_INTEGRATION=1`` is set.
2. ``pytest_configure`` prepends a poisoned ``claude`` shim to ``PATH`` in that
   same default lane, so nothing -- not the known tests, not code written
   tomorrow -- can resolve a real binary by name.

See ``tests/spend_guard.py`` for the mechanics.
"""

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claudestream._async_session import AsyncSession
from claudestream._options import SessionConfig
from tests.spend_guard import (
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


def make_test_session(config=None, **kwargs) -> AsyncSession:
    """Create an AsyncSession with mocked binary/version/profile resolution.

    The three mocks prevent the constructor from searching for a real Claude
    binary, running ``--version``, or resolving a real profile directory.

    Accepts either a ``SessionConfig`` directly or keyword arguments that
    are forwarded to ``SessionConfig()``.  When using kwargs, ``model``
    defaults to ``"haiku"`` and ``profile`` defaults to ``"test"``.
    """
    if config is None:
        model = kwargs.pop("model", "haiku")
        profile = kwargs.pop("profile", "test")
        # Pop binary -- it's set via find_binary mock, not needed in config
        kwargs.pop("binary", None)
        config = SessionConfig(model=model, profile=profile, **kwargs)
    with patch("claudestream._async_session.find_binary", return_value="/fake/claude"), \
         patch("claudestream._async_session.check_version", new_callable=AsyncMock, return_value="2.1.0"), \
         patch("claudewheel.profile.resolve_profile", return_value={}):
        session = AsyncSession(config)
    return session