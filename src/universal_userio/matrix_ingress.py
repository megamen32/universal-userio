"""Matrix read ingress owned by Universal UserIO."""
from __future__ import annotations

import logging
import os
import signal
import threading
import time

from .gmail_ingress import UserIOIngressClient
from .channels.matrix import MatrixMessage, MatrixReader

log = logging.getLogger("userio.matrix_ingress")


def poll_once(sink: UserIOIngressClient, reader: MatrixReader, *, limit: int = 100) -> int:
    cursor = sink.cursor("matrix")
    messages, nxt = reader.poll(cursor, limit=limit)
    for message in messages:
        sink.send_message(
            source="matrix", account_id="matrix", route_id="matrix-read-only",
            message_id=f"{message.room_id}:{message.event_id}",
            sender=message.sender, body=message.body,
        )
    if nxt:
        sink.set_cursor("matrix", nxt)
    return len(messages)


def main() -> int:
    logging.basicConfig(level=os.environ.get("USERIO_LOG_LEVEL", "INFO"))
    token = os.environ["USERIO_API_TOKEN"]
    sink = UserIOIngressClient(os.environ.get("USERIO_INGRESS_URL", "http://127.0.0.1:18093"), token)
    user_id = os.environ["USERIO_MATRIX_USER_ID"]
    reader = MatrixReader(
        os.environ["USERIO_MATRIX_HOMESERVER"], os.environ["USERIO_MATRIX_ACCESS_TOKEN"],
        tuple(x.strip() for x in os.environ["USERIO_MATRIX_ROOM_IDS"].split(",") if x.strip()), user_id,
    )
    sink.register_account(
        account_id="matrix", provider="matrix", display_name=user_id, can_read=True, can_reply=False,
        credential_ref="env:USERIO_MATRIX_ACCESS_TOKEN",
    )
    interval = float(os.environ.get("USERIO_MATRIX_POLL_INTERVAL_SECONDS", "2"))
    limit = int(os.environ.get("USERIO_MATRIX_POLL_LIMIT", "100"))
    max_backoff = float(os.environ.get("USERIO_MATRIX_MAX_BACKOFF_SECONDS", "60"))
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    failures = 0
    while not stop.is_set():
        try:
            delivered = poll_once(sink, reader, limit=limit)
            if delivered:
                log.info("matrix ingress delivered %d message(s)", delivered)
            failures = 0
            stop.wait(interval)
        except Exception as error:
            failures += 1
            delay = min(max_backoff, max(interval, 2 ** min(failures, 6)))
            log.warning("matrix poll failed; retrying in %.1fs: %s", delay, error)
            stop.wait(delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
