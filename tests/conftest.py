"""Shared test fixtures for claudestream."""

from unittest.mock import AsyncMock, patch

from claudestream._async_session import AsyncSession
from claudestream._options import SessionConfig


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