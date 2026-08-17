"""Transport adapters; credentials and provider URLs stay outside the AI domain."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import InboxMessage


def inbox_message_from_envelope(payload: Mapping[str, Any], *, received_at: float) -> InboxMessage:
    if payload.get("schema") != "universal.inbox.message.v1":
        raise ValueError("unsupported inbox schema")
    source = str(payload.get("source") or "").strip().lower()
    message_id = str(payload.get("message_id") or "").strip()
    sender = str(payload.get("sender") or "").strip()
    body = str(payload.get("body") or "").strip()
    if source not in {"telegram", "matrix", "whatsapp", "vk", "phone", "email", "gmail"}:
        raise ValueError("unsupported message source")
    if not message_id or not sender or not body:
        raise ValueError("inbox message requires message_id, sender and body")
    return InboxMessage(source, message_id, sender, body, received_at)


@dataclass(frozen=True, slots=True)
class NoticePlaceRoute:
    event_url: str
    token: str
    project: str
    recipient: str = "userio"
    severity: str = "notice"


class NoticePlaceOutboxClient:
    """Emit a policy-bound reply intent; the route owns every destination detail."""

    def __init__(self, routes: Mapping[str, NoticePlaceRoute], *, runner: Any = urllib.request.urlopen) -> None:
        self._routes = dict(routes)
        self._runner = runner

    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        route = self._routes.get(route_id)
        if route is None:
            raise ValueError("unknown UserIO route")
        dedup = f"userio:{conversation_id}:{draft_id}"
        payload = {
            "schema": "notify.event.v1",
            "project": route.project,
            "recipient": route.recipient,
            "kind": "notification",
            "severity": route.severity,
            "title": "UserIO approved reply",
            "body": body,
            "dedup_key": dedup,
            "correlation_id": conversation_id,
            "event_type": "userio.reply.v1",
            "producer": "universal-userio",
            "plugin": "userio",
        }
        request = urllib.request.Request(
            route.event_url.rstrip("/") + "/v1/events",
            data=json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {route.token}",
                "Idempotency-Key": "userio-" + hashlib.sha256(dedup.encode()).hexdigest(),
            },
            method="POST",
        )
        try:
            with self._runner(request, timeout=8) as response:
                if int(response.status) != 202:
                    raise RuntimeError(f"NoticePlace returned HTTP {response.status}")
                result = json.loads(response.read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"NoticePlace returned HTTP {error.code}") from error
        event_id = result.get("event_id") if isinstance(result, dict) else None
        if not isinstance(event_id, str) or not event_id:
            raise RuntimeError("NoticePlace returned invalid acceptance receipt")
        return event_id
