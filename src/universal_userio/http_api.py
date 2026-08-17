"""Small authenticated HTTP boundary for the UserIO business flow."""

from __future__ import annotations

import hmac
import json
import time
from http.server import BaseHTTPRequestHandler
from typing import Type
from urllib.parse import urlparse

from .adapters import inbox_message_from_envelope
from .service import UserIOService


def handler(service: UserIOService, *, token: str) -> Type[BaseHTTPRequestHandler]:
    class UserIOHandler(BaseHTTPRequestHandler):
        def _authorized(self) -> bool:
            presented = self.headers.get("Authorization", "")
            return hmac.compare_digest(presented, f"Bearer {token}")

        def _reply(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def do_POST(self) -> None:  # noqa: N802
            if not self._authorized():
                self._reply(401, {"error": "unauthorized"})
                return
            path = urlparse(self.path).path
            try:
                if path == "/v1/messages":
                    payload = self._json()
                    route_id = str(payload.get("route_id") or "")
                    if not route_id:
                        raise ValueError("route_id required")
                    message = inbox_message_from_envelope(payload.get("message") or {}, received_at=time.time())
                    conversation_id, accepted = service.receive(message, route_id=route_id)
                    draft = service.propose(conversation_id, message) if accepted else None
                    self._reply(202, {"conversation_id": conversation_id, "accepted": accepted, "draft": None if draft is None else {"id": draft.id, "body": draft.body, "status": draft.status}})
                    return
                if path.startswith("/v1/drafts/") and path.endswith("/approve"):
                    draft_id = path.removeprefix("/v1/drafts/").removesuffix("/approve").strip("/")
                    draft = service.approve(draft_id)
                    self._reply(202, {"id": draft.id, "status": draft.status})
                    return
                self._reply(404, {"error": "not found"})
            except (KeyError, ValueError) as error:
                self._reply(400, {"error": str(error)})

        def do_GET(self) -> None:  # noqa: N802
            if not self._authorized():
                self._reply(401, {"error": "unauthorized"})
                return
            conversation_id = urlparse(self.path).path.removeprefix("/v1/conversations/")
            if not conversation_id or conversation_id == self.path:
                self._reply(404, {"error": "not found"})
                return
            record = service._store.conversation(conversation_id)
            self._reply(200 if record else 404, record or {"error": "conversation not found"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return UserIOHandler
