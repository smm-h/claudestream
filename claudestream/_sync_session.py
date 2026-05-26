"""Synchronous session wrapper that bridges the async Claude Code stream-json protocol to a blocking iterator-based interface."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Iterator
from typing import Any, Callable

from claudestream.events import AskResult, Event, Result
from claudestream._async_session import AsyncSession, ClaudeStreamError
from claudestream._options import SessionConfig

log = logging.getLogger("claudestream")

_SENTINEL = object()  # Marks end of iteration


class SyncSession:
    """Synchronous session managing a Claude Code subprocess.

    Wraps AsyncSession by running it on a dedicated event loop thread.

    Usage::

        config = SessionConfig(model="sonnet", profile="default")
        with SyncSession(config) as session:
            for event in session.send("hello"):
                print(event)
    """

    def __init__(self, config: SessionConfig):
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._async_session: AsyncSession | None = None
        self._loop_ready = threading.Event()
        self._started = False

    def _run_loop(self) -> None:
        """Target for the event loop thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Start the event loop thread if not already running."""
        if self._loop is None:
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
            self._loop_ready.wait()
        return self._loop

    def _run_coro(self, coro):
        """Run a coroutine on the event loop thread and wait for the result."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()

    # --- Context manager ---

    def __enter__(self) -> SyncSession:
        loop = self._ensure_loop()
        self._async_session = AsyncSession(self._config)
        self._run_coro(self._async_session._start())
        self._started = True
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def close(self) -> None:
        """Shut down the session, subprocess, and event loop thread."""
        if self._async_session and self._started:
            try:
                self._run_coro(self._async_session.close())
            except Exception:
                pass
            self._async_session = None
            self._started = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread:
                self._thread.join(timeout=5.0)
            self._loop = None
            self._thread = None

    # --- Properties ---

    @property
    def session_id(self) -> str | None:
        return self._async_session.session_id if self._async_session else None

    @property
    def model_name(self) -> str | None:
        return self._async_session.model_name if self._async_session else None

    @property
    def tools(self) -> list[str]:
        return self._async_session.tools if self._async_session else []

    @property
    def claude_version(self) -> str | None:
        return self._async_session.claude_version if self._async_session else None

    @property
    def last_result(self) -> Result | None:
        return self._async_session.last_result if self._async_session else None

    # --- Cancel ---

    def cancel(self, force: bool = False) -> None:
        """Cancel the current operation.

        Args:
            force: If False, close stdin (graceful). If True, terminate subprocess.
        """
        if self._async_session:
            self._run_coro(self._async_session.cancel(force=force))

    # --- Sending messages ---

    def ask(self, prompt: str) -> AskResult:
        """Send a prompt and return the complete response text with metadata."""
        if not self._async_session:
            raise RuntimeError("Session not started. Use 'with SyncSession() as session:'")
        return self._run_coro(self._async_session.ask(prompt))

    def send(self, prompt: str, *, raw: bool = False) -> Iterator[Event]:
        """Send a message and yield events until the turn completes.

        Args:
            prompt: The message to send.
            raw: If True, yield raw protocol events. If False, yield flattened events.

        Yields:
            Event objects until a Result event is received.
        """
        if not self._async_session:
            raise RuntimeError("Session not started. Use 'with SyncSession() as session:'")

        q: queue.Queue = queue.Queue()

        async def _drain():
            try:
                async for event in self._async_session.send(prompt, raw=raw):
                    q.put(event)
            except Exception as e:
                q.put(e)
            finally:
                q.put(_SENTINEL)

        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(_drain(), loop)

        while True:
            try:
                item = q.get(timeout=1.0)
            except queue.Empty:
                if future.done():
                    # Async task finished without sending sentinel -- check for error
                    exc = future.exception()
                    if exc is not None:
                        raise exc
                    break
                continue
            if item is _SENTINEL:
                break
            if isinstance(item, Exception):
                raise item
            yield item

    # --- Callbacks ---

    def on(self, event_type: type[Event], handler: Callable[[Any], None]) -> None:
        """Register a callback for a specific event type."""
        if not self._async_session:
            raise RuntimeError("Session not started. Use 'with SyncSession() as session:'")
        self._async_session.on(event_type, handler)

    # --- Permission responses ---

    def respond_allow(self, request_id: str, updated_input: dict) -> None:
        """Allow a permission request."""
        if not self._async_session:
            raise RuntimeError("Session not started")
        self._run_coro(self._async_session.respond_allow(request_id, updated_input))

    def respond_deny(self, request_id: str, message: str = "Denied by user") -> None:
        """Deny a permission request."""
        if not self._async_session:
            raise RuntimeError("Session not started")
        self._run_coro(self._async_session.respond_deny(request_id, message))
