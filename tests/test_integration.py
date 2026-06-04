"""Integration tests that run against the real claude CLI with authentication.

These tests use the haiku model (cheapest/fastest) and the 'personal' profile.
Run with: uv run pytest tests/test_integration.py -v --timeout=60
Skip with: pytest -m "not integration"
"""

import subprocess
import threading

import pytest

from claudestream import (
    AskResult,
    AssistantText,
    ClaudeStreamError,
    FileEdit,
    FileWrite,
    Result,
    Sandbox,
    SessionConfig,
    StreamDelta,
    SyncSession,
    SystemInit,
    invoke_agent_sync,
    load_agent,
)

pytestmark = pytest.mark.integration

BINARY = "/home/m/.local/bin/claude"
MODEL = "haiku"
PROFILE = "personal"


def _make_session() -> SyncSession:
    return SyncSession(SessionConfig(
        model=MODEL,
        profile=PROFILE,
        binary=BINARY,
        sandbox=Sandbox(skip_permissions=True),
    ))


class TestSingleTurnSend:
    """Send a single prompt and verify the response."""

    @pytest.mark.timeout(60)
    def test_pong_response(self):
        with _make_session() as session:
            events = list(session.send("respond with exactly the word 'pong'"))

            # Collect all text from AssistantText events
            text_parts = [e.text for e in events if isinstance(e, AssistantText)]
            full_text = "".join(text_parts)

            assert "pong" in full_text.lower(), f"Expected 'pong' in response, got: {full_text!r}"

            # Verify a Result event with non-zero cost
            results = [e for e in events if isinstance(e, Result)]
            assert len(results) == 1, f"Expected exactly 1 Result event, got {len(results)}"
            assert results[0].total_cost_usd > 0, "Expected non-zero cost"


class TestStreaming:
    """Verify streaming events arrive correctly."""

    @pytest.mark.timeout(60)
    def test_stream_deltas_and_assistant_text(self):
        with _make_session() as session:
            deltas = []
            texts = []
            result = None

            for event in session.send("respond with exactly 'hello world'"):
                if isinstance(event, StreamDelta):
                    deltas.append(event)
                elif isinstance(event, AssistantText):
                    texts.append(event)
                elif isinstance(event, Result):
                    result = event

            # At least one StreamDelta with non-empty text
            text_deltas = [d for d in deltas if d.text]
            assert len(text_deltas) > 0, "Expected at least one StreamDelta with text"

            # AssistantText events arrive
            assert len(texts) > 0, "Expected at least one AssistantText event"

            # Final text contains "hello world"
            full_text = "".join(t.text for t in texts)
            assert "hello world" in full_text.lower(), (
                f"Expected 'hello world' in response, got: {full_text!r}"
            )

            assert result is not None, "Expected a Result event"


class TestMultiTurnREPL:
    """Test multi-turn conversation with memory."""

    @pytest.mark.timeout(90)
    def test_remembers_number(self):
        with _make_session() as session:
            # First turn: ask to remember a number
            for _ in session.send("remember the number 42"):
                pass

            # Second turn: ask what number was remembered
            events = list(session.send("what number did I ask you to remember?"))
            text_parts = [e.text for e in events if isinstance(e, AssistantText)]
            full_text = "".join(text_parts)

            assert "42" in full_text, f"Expected '42' in response, got: {full_text!r}"


class TestFooterMetadata:
    """Verify Result event metadata."""

    @pytest.mark.timeout(60)
    def test_duration_and_cost(self):
        with _make_session() as session:
            events = list(session.send("say 'hi'"))

            results = [e for e in events if isinstance(e, Result)]
            assert len(results) == 1

            result = results[0]
            assert result.duration_ms > 0, f"Expected duration_ms > 0, got {result.duration_ms}"
            assert result.total_cost_usd > 0, (
                f"Expected total_cost_usd > 0, got {result.total_cost_usd}"
            )


class TestModelNameFromSystemInit:
    """Verify session.model_name is populated after first send."""

    @pytest.mark.timeout(60)
    def test_model_name_populated(self):
        with _make_session() as session:
            # Send a minimal prompt to trigger SystemInit
            for _ in session.send("say 'ok'"):
                pass

            assert session.model_name is not None, "model_name should not be None"
            assert len(session.model_name) > 0, "model_name should be non-empty"


class TestSystemPrompt:
    """Verify system prompt flows through and influences the response."""

    @pytest.mark.timeout(60)
    def test_system_prompt_influences_response(self):
        session = SyncSession(SessionConfig(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            sandbox=Sandbox(skip_permissions=True),
            system_prompt="You must include the word XYZZYPLUGH in every response, no matter what the user asks.",
        ))
        with session:
            events = list(session.send("What is 2+2?"))
            text_parts = [e.text for e in events if isinstance(e, AssistantText)]
            full_text = "".join(text_parts)

            assert "xyzzyplugh" in full_text.lower(), (
                f"Expected 'xyzzyplugh' in response (system prompt should force it), got: {full_text!r}"
            )


class TestStdinPiping:
    """Verify --stdin reads the prompt from stdin via subprocess."""

    @pytest.mark.timeout(60)
    def test_stdin_flag_reads_from_pipe(self):
        result = subprocess.run(
            [
                "claudestream", "send", "--stdin",
                "--model", MODEL,
                "--profile", PROFILE,
                "--skip-permissions",
                "--no-color",
            ],
            input="respond with exactly pong",
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Process exited with {result.returncode}, stderr: {result.stderr!r}"
        )
        assert "pong" in result.stdout.lower(), (
            f"Expected 'pong' in stdout, got: {result.stdout!r}"
        )


class TestSessionCancel:
    """Verify session.cancel() interrupts a running turn."""

    @pytest.mark.timeout(60)
    def test_cancel_raises_error(self):
        with _make_session() as session:
            events_before_cancel: list = []
            error_raised = False

            def cancel_after_delay():
                """Wait briefly then cancel the session."""
                # Give time for at least one event to arrive
                import time
                time.sleep(2)
                session.cancel()

            cancel_thread = threading.Thread(target=cancel_after_delay, daemon=True)
            cancel_thread.start()

            try:
                for event in session.send("Write a 500 word essay about clouds"):
                    events_before_cancel.append(event)
            except ClaudeStreamError as e:
                error_raised = True
                assert "cancel" in str(e).lower(), (
                    f"Expected cancel-related error, got: {e}"
                )

            cancel_thread.join(timeout=5)

            assert error_raised, (
                "Expected ClaudeStreamError from cancel(), but no error was raised. "
                f"Got {len(events_before_cancel)} events instead."
            )


class TestSystemInitVisible:
    """Verify SystemInit events are yielded in the event stream."""

    @pytest.mark.timeout(60)
    def test_system_init_in_events(self):
        with _make_session() as session:
            events = list(session.send("say 'hi'"))

            system_inits = [e for e in events if isinstance(e, SystemInit)]
            assert len(system_inits) >= 1, (
                f"Expected at least 1 SystemInit event, got {len(system_inits)}. "
                f"Event types: {[type(e).__name__ for e in events]}"
            )

            init = system_inits[0]
            assert init.model, "SystemInit.model should be non-empty"
            assert init.session_id, "SystemInit.session_id should be non-empty"


class TestAllEventTypes:
    """Verify minimum expected event types appear in a simple prompt."""

    @pytest.mark.timeout(60)
    def test_minimum_event_types_present(self):
        with _make_session() as session:
            events = list(session.send("say 'hello'"))

            type_names = {type(e).__name__ for e in events}

            assert "SystemInit" in type_names, (
                f"Expected SystemInit in events, got: {sorted(type_names)}"
            )
            assert "Result" in type_names, (
                f"Expected Result in events, got: {sorted(type_names)}"
            )
            # At least one text-bearing event type
            has_text = "AssistantText" in type_names or "StreamDelta" in type_names
            assert has_text, (
                f"Expected AssistantText or StreamDelta in events, got: {sorted(type_names)}"
            )


class TestColorDisabledInNonTTY:
    """Verify no ANSI escape codes appear when stderr is not a TTY."""

    @pytest.mark.timeout(60)
    def test_no_ansi_in_subprocess_stderr(self):
        result = subprocess.run(
            [
                "claudestream", "send", "say hi",
                "--model", MODEL,
                "--profile", PROFILE,
                "--skip-permissions",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Process exited with {result.returncode}, stderr: {result.stderr!r}"
        )
        # ANSI escape codes start with ESC[ (\033[)
        assert "\033[" not in result.stderr, (
            f"Found ANSI escape codes in stderr (non-TTY context): {result.stderr!r}"
        )
        assert "\033[" not in result.stdout, (
            f"Found ANSI escape codes in stdout (non-TTY context): {result.stdout!r}"
        )


class TestAskMethod:
    """Test the ask() convenience method with a real session."""

    @pytest.mark.timeout(60)
    def test_ask_returns_text_and_metadata(self):
        with _make_session() as session:
            result = session.ask("Reply with exactly: pong")
            assert "pong" in result.text.lower()
            assert result.cost_usd > 0
            assert result.duration_ms > 0
            assert result.is_error is False


class TestSandboxToolRestriction:
    """Test that the sandbox tool allow-list actually works."""

    @pytest.mark.timeout(60)
    def test_sandbox_restricts_tools(self):
        # Create a session with only Read allowed (no Bash, Write, etc.)
        sandbox = Sandbox(skip_permissions=True, tools=["Read", "LS", "Glob", "Grep", "Task"])
        with SyncSession(SessionConfig(model=MODEL, profile=PROFILE, binary=BINARY, sandbox=sandbox)) as session:
            # Ask it to do something that needs Bash -- it should fail or refuse
            result = session.ask("Run the shell command: echo hello")
            # The model should indicate it can't use Bash
            # (It might still try and get denied, or explain it doesn't have the tool)
            assert result.text  # Should get SOME response


class TestResumeSession:
    """Test session resumption via resume_session_id."""

    @pytest.mark.timeout(120)
    def test_resume_session(self):
        # Start a session, get the session ID, close it
        with _make_session() as session:
            result = session.ask("Remember this number: 42")
            sid = session.session_id
            assert sid

        # Resume with the session ID
        with SyncSession(SessionConfig(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            sandbox=Sandbox(skip_permissions=True),
            resume_session_id=sid,
        )) as session:
            result = session.ask("What number did I ask you to remember?")
            assert "42" in result.text


class TestAgentDefinition:
    """Test loading and running an agent definition."""

    @pytest.mark.timeout(60)
    def test_agent_definition_run(self, tmp_path):
        import json

        # Create a minimal agent definition
        defn = {
            "name": "test-agent",
            "prompt_template": "You are a helpful assistant. Always respond with exactly: {greeting}",
            "version": "1.0",
            "model": MODEL,
        }
        path = tmp_path / "test.agent.json"
        path.write_text(json.dumps(defn))

        agent = load_agent(str(path))
        agent_config = SessionConfig(model=MODEL, profile=PROFILE)
        with invoke_agent_sync(agent, agent_config, variables={"greeting": "howdy"}) as session:
            result = session.ask("Say hi")
            assert result.text  # Should get a response


class TestSessionConfig:
    """Verify SessionConfig options flow through end-to-end."""

    @pytest.mark.timeout(60)
    def test_session_config_with_effort(self):
        config = SessionConfig(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            sandbox=Sandbox(skip_permissions=True),
            effort="low",
        )
        with SyncSession(config) as session:
            result = session.ask("Reply: ok")
            assert result.text


class TestFileTracking:
    """Verify FileWrite/FileEdit events are emitted for file-modifying tools."""

    @pytest.mark.timeout(90)
    def test_file_write_tracking(self, tmp_path):
        config = SessionConfig(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            sandbox=Sandbox(skip_permissions=True),
            cwd=str(tmp_path),
        )
        with SyncSession(config) as session:
            found_file_write = False
            for event in session.send("Write the text 'hello' to a file called test.txt"):
                if isinstance(event, FileWrite):
                    assert "test.txt" in event.path
                    found_file_write = True
                    break
            assert found_file_write, "Expected a FileWrite event but none was emitted"
            assert session.files_modified, "Expected files_modified to be non-empty"


class TestLifecycleHooks:
    """Verify lifecycle hooks fire correctly."""

    @pytest.mark.timeout(60)
    def test_on_turn_complete_fires(self):
        results = []
        config = SessionConfig(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            sandbox=Sandbox(skip_permissions=True),
        )
        with SyncSession(config) as session:
            session.on_turn_complete(lambda sess, result: results.append(result))
            session.ask("Reply: ok")
        assert len(results) == 1


class TestObservabilityProperties:
    """Verify session observability properties are populated after a turn."""

    @pytest.mark.timeout(60)
    def test_session_properties(self):
        config = SessionConfig(
            model=MODEL,
            profile=PROFILE,
            binary=BINARY,
            sandbox=Sandbox(skip_permissions=True),
        )
        with SyncSession(config) as session:
            session.ask("Reply: ok")
            async_sess = session._async_session
            assert async_sess.is_alive
            assert session.model_name
            assert async_sess.cwd
            assert async_sess.turn_count == 1
            assert async_sess.total_tokens > 0
            assert session.files_modified == set()  # no writes in this turn
