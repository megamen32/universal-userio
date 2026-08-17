"""Small authenticated HTTP boundary for the UserIO business flow."""

from __future__ import annotations

import hmac
import json
import mimetypes
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Type
from urllib.parse import parse_qs, urlparse

from .adapters import inbox_message_from_envelope
from .mcp_surface import UserIOMcpSurface
from .service import UserIOService


_STATIC_ROOT = Path(__file__).with_name("static")


def handler(service: UserIOService, *, token: str) -> Type[BaseHTTPRequestHandler]:
    surface = UserIOMcpSurface(service._store, service)
    class UserIOHandler(BaseHTTPRequestHandler):
        def _authorized(self, *, allow_proxy: bool = False) -> bool:
            presented = self.headers.get("Authorization", "")
            return hmac.compare_digest(presented, f"Bearer {token}") or (
                allow_proxy and self.headers.get("X-UserIO-Authenticated") == "1"
            )

        def _reply(self, status: int, payload: dict) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _html(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _static(self, path: str) -> bool:
            relative = path.lstrip("/") or "index.html"
            candidate = (_STATIC_ROOT / relative).resolve()
            if _STATIC_ROOT not in candidate.parents and candidate != _STATIC_ROOT:
                return False
            if not candidate.is_file():
                return False
            body = candidate.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(str(candidate))[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True

        def _json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict):
                raise ValueError("JSON object required")
            return value

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._authorized(allow_proxy=path != "/mcp"):
                self._reply(401, {"error": "unauthorized"})
                return
            try:
                if path == "/mcp":
                    request = self._json()
                    request_id = request.get("id")
                    if request.get("jsonrpc") != "2.0" or request_id is None:
                        self._reply(400, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid Request"}})
                        return
                    method = request.get("method")
                    if method == "initialize":
                        result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "universal-userio", "version": "0.1.0"}, "capabilities": {"tools": {}}}
                    elif method in {"tools/list", "tools/call"}:
                        result = surface.dispatch(method, request.get("params", {}))
                    else:
                        self._reply(200, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
                        return
                    self._reply(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
                    return
                if path == "/v1/messages":
                    payload = self._json()
                    route_id = str(payload.get("route_id") or "")
                    if not route_id:
                        raise ValueError("route_id required")
                    message = inbox_message_from_envelope(payload.get("message") or {}, received_at=time.time())
                    conversation_id, accepted = service.receive(message, route_id=route_id)
                    draft = None
                    conversation = service._store.conversation(conversation_id)
                    if accepted and conversation and conversation["response_mode"] == "auto_send":
                        draft = service.approve(service.propose(conversation_id, message).id)
                    self._reply(202, {"conversation_id": conversation_id, "accepted": accepted, "draft": None if draft is None else {"id": draft.id, "body": draft.body, "status": draft.status}})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/ai-drafts"):
                    conversation_id = path.removeprefix("/v1/conversations/").removesuffix("/ai-drafts").strip("/")
                    drafts = service.propose_from_conversation(conversation_id)
                    self._reply(202, {"drafts": [{"id": draft.id, "body": draft.body, "status": draft.status} for draft in drafts]})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/drafts"):
                    conversation_id = path.removeprefix("/v1/conversations/").removesuffix("/drafts").strip("/")
                    draft = service.create_manual_draft(conversation_id, body=str(self._json().get("body") or ""))
                    self._reply(202, {"id": draft.id, "body": draft.body, "status": draft.status})
                    return
                if path == "/v1/identities":
                    payload = self._json()
                    service._store.register_identity(
                        source=str(payload.get("source") or ""), external_id=str(payload.get("external_id") or ""),
                        identity_id=str(payload.get("identity_id") or ""), display_name=str(payload.get("display_name") or ""),
                    )
                    self._reply(202, {"accepted": True})
                    return
                if path == "/v1/accounts":
                    payload = self._json()
                    service._store.register_account(
                        account_id=str(payload.get("id") or ""), provider=str(payload.get("provider") or ""),
                        display_name=str(payload.get("display_name") or ""), can_read=bool(payload.get("can_read")),
                        can_reply=bool(payload.get("can_reply")), credential_ref=str(payload.get("credential_ref") or ""),
                        enabled=bool(payload.get("enabled", True)),
                    )
                    self._reply(202, {"accepted": True})
                    return
                if path == "/v1/reply-rules":
                    payload = self._json()
                    service._store.set_rule(
                        identity_id=str(payload.get("identity_id") or ""), source=str(payload.get("source") or ""),
                        route_id=str(payload.get("route_id") or ""), mode=str(payload.get("mode") or ""),
                    )
                    self._reply(202, {"accepted": True})
                    return
                if path == "/v1/inbox/seen":
                    payload = self._json()
                    changed = service._store.mark_seen(source=str(payload.get("source") or ""), message_id=str(payload.get("message_id") or ""))
                    self._reply(202, {"changed": changed})
                    return
                if path.startswith("/v1/drafts/") and path.endswith("/approve"):
                    draft_id = path.removeprefix("/v1/drafts/").removesuffix("/approve").strip("/")
                    draft = service.approve(draft_id)
                    self._reply(202, {"id": draft.id, "status": draft.status})
                    return
                self._reply(404, {"error": "not found"})
            except (KeyError, ValueError) as error:
                self._reply(400, {"error": str(error)})
            except RuntimeError as error:
                self._reply(502, {"error": str(error)})

        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            requested_path = urlparse(self.path).path
            if requested_path == "/" and self._static(requested_path):
                return
            if requested_path.startswith("/assets/") and self._static(requested_path):
                return
            if not self._authorized(allow_proxy=True):
                self._reply(401, {"error": "unauthorized"})
                return
            path = urlparse(self.path).path
            if path == "/v1/inbox":
                self._reply(200, {"messages": service._store.new_messages()})
                return
            if path == "/v1/accounts":
                self._reply(200, {"accounts": service._store.accounts()})
                return
            if path == "/v1/conversations":
                source = query.get("source", [""])[0].strip().lower() or None
                self._reply(200, {"conversations": service._store.conversations(source=source)})
                return
            conversation_id = path.removeprefix("/v1/conversations/")
            if not conversation_id or conversation_id == self.path:
                self._reply(404, {"error": "not found"})
                return
            record = service._store.conversation(conversation_id)
            self._reply(200 if record else 404, record or {"error": "conversation not found"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return UserIOHandler
