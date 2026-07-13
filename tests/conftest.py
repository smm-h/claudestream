"""Shared test fixtures for claudestream."""

import shutil
from unittest.mock import AsyncMock, patch

import pytest

from claudestream._async_session import AsyncSession
from claudestream._options import SessionConfig


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
    """Skip integration-marked tests when the real claude CLI/profile is absent.

    Applies only to tests carrying ``@pytest.mark.integration`` (including the
    module-level ``pytestmark``). Where the prerequisites exist (developer
    machines with a configured profile), the tests run normally.
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