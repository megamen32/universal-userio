"""Bounded waiting and per-history-window singleflight for Telegram reads."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from threading import RLock
from time import monotonic
from typing import Any, Callable, Hashable


DEFAULT_HISTORY_REFRESH_WAIT_SECONDS = 2.0


class HistoryRefreshCoordinator:
    """Join one provider refresh per owned history window.

    A caller waits briefly for freshness. On timeout, the submitted operation
    remains in the shared flight table and continues in the background; later
    reads of the same key join it instead of issuing another provider request.
    """

    def __init__(self, *, wait_seconds: float = DEFAULT_HISTORY_REFRESH_WAIT_SECONDS,
                 max_workers: int = 8, completed_result_ttl_seconds: float = 10.0,
                 max_completed_results: int = 1024,
                 monotonic_clock: Callable[[], float] = monotonic) -> None:
        if (wait_seconds < 0 or max_workers < 1 or completed_result_ttl_seconds < 0
                or max_completed_results < 1):
            raise ValueError("invalid history refresh coordinator settings")
        self._wait_seconds = wait_seconds
        self._completed_result_ttl_seconds = completed_result_ttl_seconds
        self._max_completed_results = max_completed_results
        self._clock = monotonic_clock
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="telegram-history-refresh",
        )
        self._lock = RLock()
        self._in_flight: dict[Hashable, Future[dict[str, Any]]] = {}
        self._completed_results: dict[Hashable, tuple[float, dict[str, Any]]] = {}

    def _prune_completed(self, now: float) -> None:
        expired = [
            key for key, (completed_at, _result) in self._completed_results.items()
            if now - completed_at >= self._completed_result_ttl_seconds
        ]
        for key in expired:
            self._completed_results.pop(key, None)
        while len(self._completed_results) > self._max_completed_results:
            self._completed_results.pop(next(iter(self._completed_results)))

    def _remember_completed(self, key: Hashable, future: Future[dict[str, Any]]) -> None:
        result = self._completed(future)
        with self._lock:
            if self._in_flight.get(key) is not future:
                return
            self._in_flight.pop(key, None)
            # Keep a successful result briefly so a polling read arriving just
            # after background completion observes freshness instead of
            # immediately launching an identical provider read.
            now = self._clock()
            self._prune_completed(now)
            if result.get("fresh") is True and self._completed_result_ttl_seconds:
                self._completed_results[key] = (now, result)
                self._prune_completed(now)

    def _cached_response(self, key: Hashable) -> dict[str, Any] | None:
        now = self._clock()
        self._prune_completed(now)
        completed = self._completed_results.get(key)
        if completed is None:
            return None
        completed_at, result = completed
        return {
            **result,
            "age_seconds": max(0.0, now - completed_at),
            "reused": True,
        }

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
            cached = self._cached_response(key)
            if cached is not None:
                return cached
            future = self._in_flight.get(key)
            submitted_here = future is None
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
                future.add_done_callback(
                    lambda completed: self._remember_completed(key, completed)
                )

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
            self._remember_completed(key, future)
            return self._completed(future)
        self._remember_completed(key, future)
        if not submitted_here:
            with self._lock:
                cached = self._cached_response(key)
            if cached is not None:
                return cached
        return self._completed(future)

    def shutdown(self) -> None:
        """Stop this coordinator; exposed for isolated test instances."""
        self._executor.shutdown(wait=True)


TELEGRAM_HISTORY_REFRESH = HistoryRefreshCoordinator()
