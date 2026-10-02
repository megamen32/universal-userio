"""Run async channel adapters from the sync UserIO service.

One background thread owns a dedicated event loop; sync callers submit
coroutine factories and block for the result.  Spec:
docs/2026-09-03-universal-adapters-spec.md (Phase 2).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Coroutine


class SyncChannelRunner:
    """Execute coroutine factories on a private background event loop."""

    def __init__(self, *, timeout: float = 60.0) -> None:
        self.timeout = timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        """Start the loop thread; safe to call more than once."""

        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="userio-sync-channel-runner", daemon=True
        )
        self._thread.start()
        self._ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            loop.close()

    def run(self, factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
        """Run ``factory()`` on the loop thread and return its result."""

        if self._loop is None:
            self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(factory(), self._loop)
        return future.result(self.timeout)

    def close(self) -> None:
        """Stop the loop thread; the runner cannot be reused afterwards."""

        loop, thread = self._loop, self._thread
        self._loop, self._thread = None, None
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=self.timeout)

    def __enter__(self) -> "SyncChannelRunner":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
