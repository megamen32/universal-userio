"""Permanent MAX session and bounded history/live projection into UserIO."""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import threading
import time
import urllib.request
from urllib.parse import parse_qs, urlsplit

import websocket

from .channels.max_client import MaxClient, dispatch_to_message
from .gmail_ingress import UserIOIngressClient

log = logging.getLogger("userio.max_ingress")


def _client_from_env(source) -> MaxClient:
    source = os.environ if source is None else source
    token, device_id = source["USERIO_MAX_TOKEN"], source["USERIO_MAX_DEVICE_ID"]
    socks = str(source.get("USERIO_MAX_SOCKS") or "")
    host, port = "", 0
    if socks:
        host, _, raw_port = socks.rpartition(":")
        port = int(raw_port or 1080)
    return MaxClient(token, device_id, socks_host=host, socks_port=port)


def _own_id(client: MaxClient) -> str:
    return str(((getattr(client, "profile", None) or {}).get("profile") or {}).get("contact", {}).get("id") or "")


def message_envelope(row: dict, *, chat_id: str, own_id: str, kind: str = "direct",
                     reconciliation: bool = False, title: str = "") -> dict | None:
    """Retain the provider origin; attachment tokens are never projected."""
    if row.get("id") is None:
        return None
    attachments = []
    for item in row.get("attaches") or []:
        if not isinstance(item, dict):
            continue
        typ = str(item.get("_type") or item.get("type") or "").lower()
        att = {"kind": typ or "unknown", "provider_ref": {
            "chat_id": chat_id, "message_id": str(row["id"]),
        }}
        for src, dst in [("fileId", "attachment_id"), ("photoId", "attachment_id"),
                         ("videoId", "attachment_id"), ("stickerId", "attachment_id"),
                         ("name", "filename"), ("size", "size")]:
            if src in item:
                att[dst] = item[src]
        if typ == "sticker":
            # This is the public sticker thumbnail, not a signed file token.
            url = str(item.get("url") or "")
            parts = urlsplit(url)
            if (parts.scheme == "https" and parts.netloc == "i.oneme.ru"
                    and parts.path == "/getSmile"
                    and set(parse_qs(parts.query)) <= {"smileId", "smileType"}):
                att["src"] = url
        attachments.append(att)
    text = str(row.get("text") or "")
    if not text.strip() and not attachments:
        return None
    timestamp = row.get("time")
    if not isinstance(timestamp, (int, float)) or timestamp <= 0:
        raise ValueError("MAX message has no provider timestamp")
    sender = str(row.get("sender") if row.get("sender") is not None else chat_id)
    return {
        "schema": "universal.inbox.message.v1", "source": "max",
        "message_id": f"max-{chat_id}-{row['id']}",
        "sender": sender, "body": text, "peer_id": chat_id,
        "received_at": timestamp / 1000,
        "edited_at": (row.get("updateTime") or 0) / 1000,
        "direction": "outgoing" if row.get("out") or sender == own_id else "incoming",
        "conversation_kind": kind, "attachments": attachments,
        "conversation_title": title,
        "reconciliation": reconciliation,
    }


class MaxIngressClient(UserIOIngressClient):
    def send_max_message(self, *, envelope: dict, account_id: str, route_id: str) -> None:
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps({"route_id": route_id, "account_id": account_id,
                             "message": envelope}, ensure_ascii=False).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        self._json(request)


def chat_catalog(client: MaxClient) -> tuple[list[dict], dict[str, str]]:
    chats = client.chats(count=50)
    own = _own_id(client)
    ids = sorted({int(peer) for chat in chats for peer in (chat.get("participants") or {})
                  if str(peer) != own})
    users = client.contacts(ids) if hasattr(client, "contacts") else []
    names = {}
    for user in users:
        options = user.get("names") or []
        name = next((str(x.get("name")) for x in options if x.get("name")), "")
        names[str(user.get("id"))] = name
    titles = {}
    for chat in chats:
        cid = str(chat.get("id"))
        other = next((str(k) for k in (chat.get("participants") or {}) if str(k) != own), "")
        titles[cid] = str(chat.get("title") or ("Избранное" if cid == "0" else names.get(other)) or cid)
    return chats, titles


def resolve_chat_metadata(client: MaxClient, chat_id: str) -> tuple[str, str]:
    """Resolve only the newly observed chat; never substitute a contact ID."""
    chat = client.get_chat(chat_id)
    if not chat:
        return "unknown", ""
    kind = ("group" if chat.get("type") in {"CHAT", "GROUP"} else
            "channel" if chat.get("type") == "CHANNEL" else "direct")
    if chat_id == "0":
        return kind, "Избранное"
    if chat.get("title"):
        return kind, str(chat["title"])
    other = [int(k) for k in (chat.get("participants") or {}) if str(k) != _own_id(client)]
    users = client.contacts(other[:100]) if other else []
    name = next((str(n["name"]) for u in users for n in u.get("names") or []
                 if n.get("name")), "")
    return kind, name


def group_sender_names(client: MaxClient, rows: list[dict], cache: dict[str, str]) -> None:
    own = ((getattr(client, 'profile', None) or {}).get('profile') or {}).get('contact') or {}
    own_name = next((str(n.get('name')) for n in own.get('names') or [] if n.get('name')), '')
    if own.get('id') is not None and own_name:
        cache[str(own['id'])] = own_name
    ids = sorted({str(row.get('sender')) for row in rows
                  if str(row.get('sender')).isdigit() and str(row.get('sender')) not in cache})
    if not ids or not hasattr(client, 'contacts'):
        return
    try:
        for offset in range(0, len(ids), 100):
            batch = ids[offset:offset+100]
            users = client.contacts([int(value) for value in batch])
            for value in batch:
                cache[value] = ''
            for user in users:
                name = next((str(n.get('name')) for n in user.get('names') or [] if n.get('name')), '')
                username = str(user.get('username') or '').lstrip('@')
                cache[str(user.get('id'))] = ' · '.join(x for x in (name, '@'+username if username else '') if x)
    except (TimeoutError, OSError, RuntimeError, ValueError, websocket.WebSocketException) as error:
        log.warning('MAX author metadata pending: %s', type(error).__name__)


def backfill(sink: MaxIngressClient, client: MaxClient, *, depth: int = 30,
             route_id: str = "max", account_id: str = "max",
             stop: threading.Event | None = None, author_names: dict[str, str] | None = None) -> int:
    delivered = 0
    own = _own_id(client)
    chats, titles = chat_catalog(client)
    author_names = {} if author_names is None else author_names
    for chat in chats:
        if stop is not None and stop.is_set():
            break
        if chat.get("id") is None:
            continue
        chat_id = str(chat["id"])
        kind = "group" if str(chat.get("type")) in {"CHAT", "GROUP"} else (
            "channel" if str(chat.get("type")) == "CHANNEL" else "direct")
        rows = sorted(client.history(chat_id, backward=depth), key=lambda m: m.get("time") or 0)
        if kind in {'group','channel'}:
            group_sender_names(client, rows, author_names)
        for row in rows:
            envelope = message_envelope(row, chat_id=chat_id, own_id=own, kind=kind,
                                        reconciliation=True, title=titles.get(chat_id, ""))
            if envelope is not None:
                if kind in {'group','channel'}:
                    envelope['sender_name'] = author_names.get(str(row.get('sender')), '')
                sink.send_max_message(envelope=envelope, account_id=account_id, route_id=route_id)
                delivered += 1
    return delivered


def run_loop(sink: MaxIngressClient, client: MaxClient, *, route_id: str = "max",
             account_id: str = "max", depth: int = 30, stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    delay = 3
    ready = False
    kinds = {}
    titles = {}
    author_names: dict[str, str] = {}
    while not stop.is_set():
        try:
            if not ready:
                author_names.clear()
                login = client.connect()
                if not _own_id(client):
                    raise ValueError("MAX account identity missing")
                names = (login.get("profile") or {}).get("contact", {}).get("names") or []
                name = next((str(x.get("name")) for x in names if x.get("name")), "MAX")
                sink.register_account(account_id=account_id, provider="max", display_name=name,
                                      can_reply=True, credential_ref="env:USERIO_MAX_TOKEN")
                kinds = {str(c["id"]): "group" if c.get("type") in {"CHAT", "GROUP"}
                         else "channel" if c.get("type") == "CHANNEL" else "direct"
                         for c in login.get("chats", []) if c.get("id") is not None}
                _, titles = chat_catalog(client)
                if depth:
                    backfill(sink, client, depth=depth, route_id=route_id, account_id=account_id, stop=stop, author_names=author_names)
                ready, delay = True, 3
                log.info("MAX identity and history ready")
            payload = client.dispatch(timeout=1)
            message = dispatch_to_message(payload)
            if message is None:
                continue
            chat_id = message["chat_id"]
            if hasattr(client, "get_chat") and (chat_id not in kinds or
                    not titles.get(chat_id) or titles.get(chat_id) == chat_id):
                try:
                    kind, title = resolve_chat_metadata(client, chat_id)
                    kinds[chat_id], titles[chat_id] = kind, title
                except (TimeoutError, OSError, RuntimeError, ValueError,
                        websocket.WebSocketException) as error:
                    # Metadata must not discard the real message. Retry the
                    # name lookup on the next event while retaining its origin.
                    log.warning("MAX chat metadata pending: %s", type(error).__name__)
            row = dict((payload or {}).get("message") or payload)
            envelope = message_envelope(row, chat_id=message["chat_id"], own_id=_own_id(client),
                                        kind=kinds.get(message["chat_id"], "unknown"),
                                        title=titles.get(message["chat_id"], ""))
            if envelope is not None:
                if kinds.get(chat_id) in {'group','channel'}:
                    if len(author_names) > 2000:
                        author_names.clear()
                    group_sender_names(client, [row], author_names)
                    envelope['sender_name'] = author_names.get(str(row.get('sender')), '')
                sink.send_max_message(envelope=envelope, account_id=account_id, route_id=route_id)
        except (TimeoutError, OSError, RuntimeError, ValueError, websocket.WebSocketException) as error:
            log.warning("MAX reconnect after %s", type(error).__name__)
            client.close()
            ready = False
            if stop.wait(delay):
                break
            delay = min(delay * 2, 30)
    client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", type=int, default=30, metavar="N")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.backfill <= 100:
        parser.error("backfill must be between 0 and 100")
    logging.basicConfig(level=os.environ.get("USERIO_LOG_LEVEL", "INFO"))
    route = os.environ.get("USERIO_MAX_ROUTE_ID", "max").strip() or "max"
    account = os.environ.get("USERIO_MAX_ACCOUNT_ID", "max").strip() or "max"
    client = _client_from_env(None)
    sink = MaxIngressClient(os.environ.get("USERIO_INGRESS_URL", "http://127.0.0.1:18093"),
                            os.environ["USERIO_API_TOKEN"])
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    try:
        if args.once:
            client.connect()
            backfill(sink, client, depth=args.backfill, route_id=route, account_id=account, stop=stop)
        else:
            run_loop(sink, client, route_id=route, account_id=account, depth=args.backfill, stop=stop)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
