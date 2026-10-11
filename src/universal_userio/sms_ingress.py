"""Continuous Android SMS ingress owned by Universal UserIO."""
from __future__ import annotations

import argparse
import os
import signal
import threading
import json

from .channels.sms_gateway import AndroidSmsGatewayClient
from .gmail_ingress import UserIOIngressClient


def poll_once(sink: UserIOIngressClient, gateway: AndroidSmsGatewayClient, *, user_source: str = "sms") -> int:
    delivered = 0
    accounts=getattr(sink,'accounts',lambda:[])()
    native=next((a for a in accounts if a.get('provider')=='sms' and a.get('enabled') and a.get('transport')=='native'),None)
    if native is not None and hasattr(gateway,'history'):
        cursor_key='sms-native-history:'+str(native['id'])
        raw=sink.cursor(cursor_key)
        state=json.loads(raw) if raw else {'after_id':0,'snapshot_id':0,'initialized':False}
        page=gateway.history(after_id=int(state['after_id']),snapshot_id=int(state.get('snapshot_id',0)))
        for message in page['messages']:
            sink.send_message(source=user_source,account_id=str(native['id']),route_id='sms',message_id=message.message_id,sender=message.sender,body=message.body,received_at=message.received_at,direction=message.direction,reconciliation=not state['initialized'],provider_read=message.provider_read,outbox_receipt=message.outbox_receipt)
            delivered+=1
        sink.set_cursor(cursor_key,json.dumps({'after_id':int(page['next_after_id']),'snapshot_id':int(page['snapshot_id']) if page['has_more'] else 0,'initialized':bool(state['initialized'] or not page['has_more'])}))
        native_messages=[]
    else:
        native_messages=gateway.inbound()
    for message in native_messages:
        sink.send_message(
            source=user_source,
            account_id=native['id'] if native else 'sms',
            route_id="sms",
            message_id=message.message_id,
            sender=message.sender,
            body=message.body,
            received_at=message.received_at,
        )
        delivered += 1
    # Only registered accounts of the ingress principal; no second polling worker.
    for account in accounts:
        aid=str(account.get('id') or '')
        if account.get('provider')!='sms' or not account.get('enabled') or 'read' not in account.get('capabilities',[]) or not aid.startswith('sms:') or account.get('transport')!='apk': continue
        for message in gateway.inbound(device_id=aid.removeprefix('sms:')):
            sink.send_message(source=user_source,account_id=aid,route_id='sms',message_id=message.message_id,sender=message.sender,body=message.body,received_at=message.received_at)
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
