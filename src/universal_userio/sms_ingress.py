"""Continuous Android SMS ingress owned by Universal UserIO."""
from __future__ import annotations

import argparse
import os
import signal
import threading

from .channels.sms_gateway import AndroidSmsGatewayClient
from .gmail_ingress import UserIOIngressClient


def poll_once(sink: UserIOIngressClient, gateway: AndroidSmsGatewayClient, *, user_source: str = "sms") -> int:
    delivered = 0
    for message in gateway.inbound():
        sink.send_message(
            source=user_source,
            account_id="sms",
            route_id="sms",
            message_id=message.message_id,
            sender=message.sender,
            body=message.body,
        )
        delivered += 1
    return delivered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    gateway = AndroidSmsGatewayClient(os.environ["USERIO_SMS_GATEWAY_URL"], os.environ["USERIO_SMS_GATEWAY_TOKEN"])
    sink = UserIOIngressClient(os.environ.get("USERIO_INGRESS_URL", "http://127.0.0.1:18093"), os.environ["USERIO_API_TOKEN"])
    interval = float(os.environ.get("USERIO_SMS_POLL_INTERVAL_SECONDS", "5"))
    stop = threading.Event()
    if not args.once:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        poll_once(sink, gateway)
        if args.once:
            break
        stop.wait(interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
