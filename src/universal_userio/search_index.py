"""Bounded background indexing for the owner's opt-in semantic history search.

Runs as an ordinary supervised UserIO worker, so it inherits the existing
runtime lifecycle: it starts with the backend, is restarted by the supervisor
when it exits, and stops with the container. It is not an agent loop and never
invents work: each iteration performs one bounded, checkpointed index slice and
then sleeps. Nothing happens at all until the owner has opted in.

Priority chats (the owner's own working conversations) are indexed before the
rest of the retained history, and both scoped and global coverage are reported
honestly so a partial index is never presented as a complete one.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

from .search import run_search_index, search_coverage
from .store import SQLiteUserIOStore

log = logging.getLogger("userio.search_index")

DEFAULT_SLEEP_SECONDS = 20
DEFAULT_MESSAGES = 200
DEFAULT_BUDGET_SECONDS = 240


def _positive(value: str, default: int, *, maximum: int) -> int:
    try:
        return max(1, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _seconds(value: str, default: int, *, maximum: int) -> float:
    try:
        return max(1.0, min(float(value), float(maximum)))
    except (TypeError, ValueError):
        return float(default)


class IndexWorker:
    """One bounded worker for every owner who explicitly opted in.

    Without ``--user-id`` it round-robins over the users that have their own
    semantic settings, each with its own endpoint, checkpoint and derived
    state. Nothing is inherited from the service owner, and no worker is created
    per user: the same process simply serves the next enabled account in turn.
    """

    def __init__(self, store: SQLiteUserIOStore, *, user_id: str | None = None,
                 sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
                 messages: int = DEFAULT_MESSAGES,
                 budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
        self.store = store
        self.user_id = user_id
        self.sleep_seconds, self.messages = sleep_seconds, messages
        self.budget_seconds = budget_seconds
        self.stopping = False
        self._cursor = 0

    def stop(self, *_args: object) -> None:
        self.stopping = True

    def opted_in_users(self) -> list[str]:
        """Users whose own settings enable semantic search, in a stable order."""
        if self.user_id:
            return [self.user_id]
        rows = self.store._connection.execute(
            "SELECT user_id FROM user_search_settings WHERE enabled=1 ORDER BY user_id").fetchall()
        return [str(row["user_id"]) for row in rows]

    def next_user(self) -> str | None:
        users = self.opted_in_users()
        if not users:
            return None
        self._cursor = (self._cursor + 1) % len(users)
        return users[self._cursor]

    def run_once(self, user_id: str | None = None) -> dict[str, Any]:
        """One slice for one owner. Returns the honest status even when idle."""
        target = user_id or self.next_user()
        if target is None:
            return {"indexed": 0, "chunks": 0, "failed": 0, "ran": False,
                    "reason": "no_opt_in", "complete": False}
        result = run_search_index(
            _ServiceShim(self.store), {"messages": self.messages,
                                       "budget_seconds": self.budget_seconds},
            user_id=target)
        coverage = search_coverage(self.store, user_id=target)
        return {"user_id": target, "ran": bool(result.get("ran")),
                "indexed": result.get("step", {}).get("indexed", 0),
                "chunks": result.get("step", {}).get("chunks", 0),
                "failed": result.get("step", {}).get("failed", 0),
                "reason": result.get("reason", ""),
                "complete": result.get("complete", False),
                "coverage_ratio": coverage["semantic"].get("coverage_ratio", 0.0),
                "semantic_available": coverage["semantic"].get("available", False)}

    def loop(self) -> None:
        log.info("search index worker started (nothing is indexed until an owner opts in)")
        while not self.stopping:
            try:
                summary = self.run_once()
                if summary["indexed"] or summary["failed"] or summary["reason"]:
                    log.info("search index slice: %s", json.dumps(summary))
            except Exception as error:  # noqa: BLE001 - the worker must stay up
                log.warning("search index slice failed: %s", type(error).__name__)
            for _ in range(int(self.sleep_seconds)):
                if self.stopping:
                    return
                time.sleep(1)


class _ServiceShim:
    """The index runner only needs the durable store."""

    def __init__(self, store: SQLiteUserIOStore) -> None:
        self._store = store


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("USERIO_DB_PATH", ""))
    parser.add_argument("--user-id", default=os.environ.get("USERIO_SEARCH_INDEX_USER", ""))
    parser.add_argument("--sleep-seconds", default=os.environ.get(
        "USERIO_SEARCH_INDEX_SLEEP_SECONDS", str(DEFAULT_SLEEP_SECONDS)))
    parser.add_argument("--messages", default=os.environ.get(
        "USERIO_SEARCH_INDEX_MESSAGES", str(DEFAULT_MESSAGES)))
    parser.add_argument("--budget-seconds", default=os.environ.get(
        "USERIO_SEARCH_INDEX_BUDGET_SECONDS", str(DEFAULT_BUDGET_SECONDS)))
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not arguments.db:
        raise SystemExit("USERIO_DB_PATH is required")
    store = SQLiteUserIOStore(Path(arguments.db))
    worker = IndexWorker(
        store, user_id=arguments.user_id or None,
        sleep_seconds=_seconds(arguments.sleep_seconds, DEFAULT_SLEEP_SECONDS, maximum=600),
        messages=_positive(arguments.messages, DEFAULT_MESSAGES, maximum=1000),
        budget_seconds=_seconds(arguments.budget_seconds, DEFAULT_BUDGET_SECONDS, maximum=1800))
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    if arguments.status:
        print(json.dumps(search_coverage(store, user_id=arguments.user_id or None),
                         ensure_ascii=False, indent=2))
        return 0
    if arguments.once:
        print(json.dumps(worker.run_once(), ensure_ascii=False))
        return 0
    worker.loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())