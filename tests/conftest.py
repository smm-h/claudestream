"""Shared test fixtures for claudestream."""

from unittest.mock import AsyncMock, patch

from claudestream._async_session import AsyncSession


def make_test_session(**kwargs) -> AsyncSession:
    """Create an AsyncSession with mocked binary/version/profile resolution.

    The three mocks prevent the constructor from searching for a real Claude
    binary, running ``--version``, or resolving a real profile directory.
    Extra *kwargs* are forwarded to ``AsyncSession()``.
    """
    with patch("claudestream._async_session.find_binary", return_value="/fake/claude"), \
         patch("claudestream._async_session.check_version", new_callable=AsyncMock, return_value="2.1.0"), \
         patch("claudewheel.profile.resolve_profile", return_value={}):
        session = AsyncSession(model="haiku", profile="test", binary="/fake/claude", **kwargs)
    return session