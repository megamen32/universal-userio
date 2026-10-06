"""Continuous MAX (oneme.ru) ingress owned by Universal UserIO.

Holds one logged-in MAX WebSocket session, turns DISPATCH events into normal
UserIO conversations, and (with ``--backfill N``) imports the recent history of
every chat once at startup.  Requires the same environment as the service
side: ``USERIO_MAX_TOKEN``, ``USERIO_MAX_DEVICE_ID`` and the ingress sink
``USERIO_INGRESS_URL`` + ``USERIO_API_TOKEN``.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import time

from .channels.max_client import MaxClient, dispatch_to_message
from .gmail_ingress import UserIOIngressClient

log = logging.getLogger("userio.max_ingress")


def _client_from_env(source) -> MaxClient:
    token = os.environ["USERIO_MAX_TOKEN"] if source is None else source["USERIO_MAX_TOKEN"]
    device_id = (os.environ if source is None else source).get("USERIO_MAX_DEVICE_ID", "")
    socks = str((os.environ if source is None else source).get("USERIO_MAX_SOCKS") or "")
    socks_host, socks_port = "", 0
    if socks:
        host, _, port = socks.rpartition(":")
        socks_host, socks_port = host, int(port or 1080)
    return MaxClient(token, device_id, socks_host=socks_host, socks_port=socks_port)


def _own_id(client: MaxClient) -> str:
    profile = client.profile or {}
    contact = (profile.get("profile") or {}).get("contact") or {}
    return str(contact.get("id") or "")


def backfill(sink: UserIOIngressClient, client: MaxClient, *, depth: int = 10, route_id: str = "max") -> int:
    delivered = 0
    own = _own_id(client)
    for chat in client.chats(count=50):
        chat_id = str(chat.get("id") or "")
        if not chat_id:
            continue
        for row in sorted(client.history(chat_id, backward=depth), key=lambda m: m.get("time") or 0):
            if own and str(row.get("sender") or "") == own:
                continue
            text = str(row.get("text") or "").strip()
            if not text:
                continue
            sink.send_message(
                source="max",
                account_id="max",
                route_id=route_id,
                message_id=f"max-{chat_id}-{row.get('id')}",
                sender=str(row.get("sender") or "") or chat_id,
                body=text,
            )
            delivered += 1
    return delivered


def run_loop(sink: UserIOIngressClient, client: MaxClient, *, route_id: str = "max") -> None:
    own = _own_id(client)
    log.info("MAX ingress ready; own user id: %s", own or "unknown")
    while True:
        try:
            payload = client.dispatch(timeout=25)
        except (TimeoutError, OSError, RuntimeError) as error:
            log.warning("MAX dispatch failed (%s); reconnecting", error)
            client.close()
            time.sleep(3)
            client.connect()
            own = _own_id(client)
            continue
        if payload is None:
            continue
        message = dispatch_to_message(payload)
        if message is None:
            continue
        if own and message["sender"] == own:
            continue
        sink.send_message(
            source="max",
            account_id="max",
            route_id=route_id,
            message_id=f"max-{message['chat_id']}-{message['message_id']}",
            sender=message["sender"] or message["chat_id"],
            body=message["text"],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=0, metavar="N",
                        help="import the last N messages of every chat before listening")
    parser.add_argument("--once", action="store_true", help="backfill and exit (no listener)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("USERIO_LOG_LEVEL", "INFO"))
    route_id = os.environ.get("USERIO_MAX_ROUTE_ID", "max").strip() or "max"
    client = _client_from_env(None)
    sink = UserIOIngressClient(
        os.environ.get("USERIO_INGRESS_URL", "http://127.0.0.1:18093"), os.environ["USERIO_API_TOKEN"]
    )
    client.connect()
    if args.backfill:
        delivered = backfill(sink, client, depth=args.backfill, route_id=route_id)
        log.info("MAX backfill delivered %s messages", delivered)
    if args.once:
        client.close()
        return 0
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        run_loop(sink, client, route_id=route_id)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
