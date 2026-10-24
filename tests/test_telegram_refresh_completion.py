"""Completed Telegram refresh reuse across short polling intervals."""
from __future__ import annotations

import threading
import time

from universal_userio.telegram_history_refresh import HistoryRefreshCoordinator


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_timed_out_refresh_is_reused_by_next_poll_without_duplicate_provider_call():
    clock = FakeMonotonic()
    coordinator = HistoryRefreshCoordinator(
        wait_seconds=0.01, monotonic_clock=clock,
    )
    started = threading.Event()
    release = threading.Event()
    calls = []
    key = ("user", "telegram:owned", "private-peer", None, 50)

    def refresh():
        calls.append("provider-read")
        started.set()
        assert release.wait(timeout=2)
        return {"status": "refreshed", "fresh": True, "receipt": "history-42"}

    try:
        first = coordinator.refresh(key, refresh)
        assert started.is_set()
        assert first == {
            "status": "refreshing", "fresh": False, "stale": True, "refreshing": True,
        }

        release.set()
        # The operation is now complete; its next UI/API poll must observe the
        # just-produced receipt rather than start another provider request.
        deadline = time.monotonic() + 1
        observed = None
        while time.monotonic() < deadline:
            observed = coordinator.refresh(
                key, lambda: (_ for _ in ()).throw(AssertionError("refresh restarted")),
            )
            if observed.get("receipt") == "history-42":
                break
            time.sleep(0.001)
        assert observed == {
            "status": "refreshed", "fresh": True, "receipt": "history-42",
            "stale": False, "refreshing": False, "age_seconds": 0.0, "reused": True,
        }
        assert calls == ["provider-read"]
    finally:
        release.set()
        coordinator.shutdown()


def test_expired_result_triggers_one_new_refresh_then_is_reused():
    clock = FakeMonotonic()
    coordinator = HistoryRefreshCoordinator(monotonic_clock=clock)
    calls = []
    key = ("user", "telegram:owned", "private-peer", None, 50)

    def refresh():
        calls.append(len(calls) + 1)
        return {"status": "refreshed", "fresh": True, "sequence": calls[-1]}

    try:
        assert coordinator.refresh(key, refresh)["sequence"] == 1
        reused = coordinator.refresh(key, refresh)
        assert reused["sequence"] == 1
        assert reused["reused"] is True
        assert reused["age_seconds"] == 0.0
        assert calls == [1]

        clock.advance(10.0)
        refreshed = coordinator.refresh(key, refresh)
        assert refreshed["sequence"] == 2
        assert calls == [1, 2]
        assert coordinator.refresh(key, refresh)["sequence"] == 2
        assert calls == [1, 2]
    finally:
        coordinator.shutdown()


def test_completed_results_are_isolated_by_history_window_key():
    coordinator = HistoryRefreshCoordinator(monotonic_clock=FakeMonotonic())
    calls = []

    def operation(label):
        def refresh():
            calls.append(label)
            return {"status": "refreshed", "fresh": True, "receipt": label}
        return refresh

    first_key = ("user", "telegram:owned", "peer-a", None, 50)
    second_key = ("user", "telegram:owned", "peer-b", None, 50)
    try:
        assert coordinator.refresh(first_key, operation("a"))["receipt"] == "a"
        assert coordinator.refresh(second_key, operation("b"))["receipt"] == "b"
        assert coordinator.refresh(first_key, operation("duplicate-a"))["receipt"] == "a"
        assert calls == ["a", "b"]
    finally:
        coordinator.shutdown()


def test_expired_entries_are_pruned_and_cache_size_is_bounded():
    clock = FakeMonotonic()
    coordinator = HistoryRefreshCoordinator(
        monotonic_clock=clock, max_completed_results=1,
    )
    calls = []

    def operation(label):
        def refresh():
            calls.append(label)
            return {"status": "refreshed", "fresh": True, "receipt": label}
        return refresh

    first_key = ("user", "telegram:owned", "peer-a", None, 50)
    second_key = ("user", "telegram:owned", "peer-b", None, 50)
    try:
        coordinator.refresh(first_key, operation("a"))
        coordinator.refresh(second_key, operation("b"))
        # Capacity eviction removes the oldest cached receipt.
        assert coordinator.refresh(first_key, operation("a-again"))["receipt"] == "a-again"
        assert calls == ["a", "b", "a-again"]

        clock.advance(10.0)
        assert coordinator.refresh(second_key, operation("b-after-expiry"))["receipt"] == "b-after-expiry"
        assert calls == ["a", "b", "a-again", "b-after-expiry"]
    finally:
        coordinator.shutdown()


def test_failed_refresh_is_not_cached_and_next_poll_can_retry():
    coordinator = HistoryRefreshCoordinator(monotonic_clock=FakeMonotonic())
    calls = []
    key = ("user", "telegram:owned", "private-peer", None, 50)

    def fail():
        calls.append("failed")
        raise OSError("connector read failed")

    def recover():
        calls.append("recovered")
        return {"status": "refreshed", "fresh": True, "receipt": "history-43"}

    try:
        result = coordinator.refresh(key, fail)
        assert result["status"] == "failed"
        assert result["fresh"] is False
        recovered = coordinator.refresh(key, recover)
        assert recovered["receipt"] == "history-43"
        assert calls == ["failed", "recovered"]
    finally:
        coordinator.shutdown()
