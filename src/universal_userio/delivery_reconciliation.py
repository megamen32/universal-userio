"""Bounded read-only reconciliation for already claimed Telegram sends."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable


_LOG = logging.getLogger(__name__)
MAX_AUTOMATIC_CHECKS = 6
RETRY_DELAYS_SECONDS = (30.0, 120.0, 600.0, 1800.0, 7200.0)


class TelegramDeliveryReconciliationWorker:
    """Read connector receipts for persisted sending drafts; never calls send."""

    def __init__(self, service, *, interval_seconds: float = 5.0,
                 clock: Callable[[], float] = time.time):
        self.service = service
        self.store = service._store
        self.interval_seconds = max(1.0, min(float(interval_seconds), 60.0))
        self.clock = clock
        self.worker_id = uuid.uuid4().hex
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, name="userio-telegram-delivery-reconcile", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                checked = self.run_once()
            except Exception:
                _LOG.exception("Telegram delivery reconciliation iteration failed")
                checked = 0
            if not checked:
                self.stop.wait(self.interval_seconds)

    def run_once(self) -> int:
        claimed = self.store.claim_due_telegram_delivery_reconciliations(
            worker_id=self.worker_id, now=self.clock(), limit=1, lease_seconds=120.0,
        )
        checked = 0
        for item in claimed:
            user_id = str(item["user_id"])
            draft_id = str(item["id"])
            try:
                attempts = int(item.get("delivery_attempts") or 0)
                check_number = attempts + 1
                terminal = check_number >= MAX_AUTOMATIC_CHECKS
                next_check = None
                if not terminal:
                    next_check = self.clock() + RETRY_DELAYS_SECONDS[attempts]
                result = self.service.reconcile_draft_delivery(
                    draft_id, user_id=user_id, automatic=True,
                    automatic_next_check_at=next_check,
                    automatic_terminal_unknown=terminal,
                    expected_generation=int(item["delivery_attempt_generation"]),
                    expected_lease_owner=self.worker_id,
                )
                _LOG.info(
                    "Checked Telegram delivery attempt draft=%s resolution=%s state=%s",
                    draft_id, result.get("resolution"), result.get("status"),
                )
                checked += 1
            except Exception:
                _LOG.exception("Telegram delivery check failed for an owned draft")
            finally:
                self.store.release_draft_delivery_reconciliation_claim(
                    draft_id, user_id=user_id, worker_id=self.worker_id,
                )
        return checked
