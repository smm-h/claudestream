"""Tests for session resumption (--resume flag)."""

from unittest.mock import patch

from claudestream._process import ProcessConfig
from claudestream._async_session import AsyncSession
from claudestream._sync_session import SyncSession


class TestProcessConfigResume:
    def test_resume_flag_in_argv(self):
        config = ProcessConfig(binary="claude", resume_session_id="abc-123")
        argv = config.build_argv()
        idx = argv.index("--resume")
        assert argv[idx + 1] == "abc-123"

    def test_resume_none_not_in_argv(self):
        config = ProcessConfig(binary="claude")
        argv = config.build_argv()
        assert "--resume" not in argv

    def test_resume_empty_string_not_in_argv(self):
        config = ProcessConfig(binary="claude", resume_session_id="")
        argv = config.build_argv()
        assert "--resume" not in argv


class TestAsyncSessionResume:
    def test_async_session_accepts_resume(self):
        with patch("claudestream._async_session.find_binary", return_value="/fake/claude"):
            with patch("claudestream._async_session.sandbox_to_flags", return_value=[]):
                with patch("claudewheel.profile.resolve_profile", return_value={}):
                    session = AsyncSession(
                        model="sonnet",
                        profile="test",
                        resume_session_id="sess-456",
                    )
        assert session._process_mgr.config.resume_session_id == "sess-456"


class TestSyncSessionResume:
    def test_sync_session_accepts_resume(self):
        session = SyncSession(
            model="sonnet",
            profile="test",
            resume_session_id="sess-789",
        )
        assert session._kwargs["resume_session_id"] == "sess-789"
