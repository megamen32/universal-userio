"""Bounded waiting and per-history-window singleflight for Telegram reads."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import RLock
from typing import Any, Callable, Hashable


DEFAULT_HISTORY_REFRESH_WAIT_SECONDS = 2.0


class HistoryRefreshCoordinator:
    """Join one provider refresh per owned history window.

    A caller waits briefly for freshness. On timeout, the submitted operation
    remains in the shared flight table and continues in the background; later
    reads of the same key join it instead of issuing another provider request.
    """

    def __init__(self, *, wait_seconds: float = DEFAULT_HISTORY_REFRESH_WAIT_SECONDS,
                 max_workers: int = 8) -> None:
        if wait_seconds < 0 or max_workers < 1:
            raise ValueError("invalid history refresh coordinator settings")
        self._wait_seconds = wait_seconds
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="telegram-history-refresh",
        )
        self._lock = RLock()
        self._in_flight: dict[Hashable, Future[dict[str, Any]]] = {}

    def _discard(self, key: Hashable, future: Future[dict[str, Any]]) -> None:
        with self._lock:
            if self._in_flight.get(key) is future:
                self._in_flight.pop(key, None)

    @staticmethod
    def _completed(future: Future[dict[str, Any]]) -> dict[str, Any]:
        try:
            result = future.result()
        except Exception as error:
            return {
                "status": "failed",
                "fresh": False,
                "stale": True,
                "refreshing": False,
                "error": type(error).__name__,
            }
        if not isinstance(result, dict):
            return {
                "status": "failed",
                "fresh": False,
                "stale": True,
                "refreshing": False,
                "error": "InvalidRefreshResult",
            }
        fresh = result.get("fresh") is True
        return {
            **result,
            "stale": not fresh,
            "refreshing": False,
        }

    def refresh(self, key: Hashable, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            future = self._in_flight.get(key)
            if future is None:
                try:
                    future = self._executor.submit(operation)
                except RuntimeError as error:
                    return {
                        "status": "failed",
                        "fresh": False,
                        "stale": True,
                        "refreshing": False,
                        "error": type(error).__name__,
                    }
                self._in_flight[key] = future
                future.add_done_callback(lambda completed: self._discard(key, completed))

        try:
            future.result(timeout=self._wait_seconds)
        except FutureTimeout:
            # Distinguish a just-completed operation from work that really
            # remains in flight at the two-second boundary.
            if not future.done():
                return {
                    "status": "refreshing",
                    "fresh": False,
                    "stale": True,
                    "refreshing": True,
                }
        except Exception:
            # A completed provider failure is stale-but-terminal for this
            # read; return its safe status instead of leaking the exception.
            return self._completed(future)
        return self._completed(future)

    def shutdown(self) -> None:
        """Stop this coordinator; exposed for isolated test instances."""
        self._executor.shutdown(wait=True)


TELEGRAM_HISTORY_REFRESH = HistoryRefreshCoordinator()
