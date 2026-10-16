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
    def __init__(self, store: SQLiteUserIOStore, *, user_id: str | None = None,
                 sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
                 messages: int = DEFAULT_MESSAGES,
                 budget_seconds: float = DEFAULT_BUDGET_SECONDS) -> None:
        self.store = store
        self.user_id = user_id
        self.sleep_seconds, self.messages = sleep_seconds, messages
        self.budget_seconds = budget_seconds
        self.stopping = False

    def stop(self, *_args: object) -> None:
        self.stopping = True

    def enabled(self) -> bool:
        from . import semantic_index
        settings = semantic_index.resolve_settings(self.store, user_id=self.user_id)
        return settings.enabled and settings.configured()

    def run_once(self) -> dict[str, Any]:
        """One slice. Returns the honest status even when it did nothing."""
        result = run_search_index(
            _ServiceShim(self.store), {"messages": self.messages,
                                       "budget_seconds": self.budget_seconds},
            user_id=self.user_id)
        coverage = search_coverage(self.store, user_id=self.user_id)
        return {"indexed": result.get("step", {}).get("indexed", 0),
                "chunks": result.get("step", {}).get("chunks", 0),
                "failed": result.get("step", {}).get("failed", 0),
                "complete": result.get("complete", False),
                "coverage_ratio": coverage["text_only"] and 0.0 or coverage["semantic"].get(
                    "coverage_ratio", 0.0),
                "semantic_available": coverage["semantic"].get("available", False)}

    def loop(self) -> None:
        log.info("search index worker started (opt-in is off until an owner enables it)")
        while not self.stopping:
            try:
                if self.enabled():
                    summary = self.run_once()
                    if summary["indexed"] or summary["failed"]:
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