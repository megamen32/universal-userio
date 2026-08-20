"""Small authenticated HTTP boundary for the UserIO business flow."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Type
from urllib.parse import parse_qs, unquote, urlparse

from .adapters import inbox_message_from_envelope
from .mcp_surface import UserIOMcpSurface
from .service import UserIOService


_STATIC_ROOT = Path(__file__).with_name("static")


def handler(service: UserIOService, *, token: str, vkid_app_id: str = "") -> Type[BaseHTTPRequestHandler]:
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
                if path == "/v1/vk/accounts":
                    payload = self._json()
                    external_id = str(payload.get("user_id") or "").strip()
                    display_name = str(payload.get("display_name") or "VK account").strip()
                    if not external_id:
                        raise ValueError("user_id required")
                    # VK ID confirms the identity only. Message access belongs to the
                    # user-owned browser extension and must not be implied here.
                    service._store.register_account(
                        account_id=f"vk:{external_id}", provider="vk", display_name=display_name,
                        can_read=False, can_reply=False, credential_ref=f"vkid:{external_id}", enabled=True,
                    )
                    self._reply(202, {"accepted": True, "account_id": f"vk:{external_id}", "mode": "vkid_identity_only"})
                    return
                if path == "/v1/gmail/accounts":
                    payload = self._json()
                    alias = str(payload.get("account") or "").strip()
                    allowed = {item.strip() for item in os.getenv("UNIVERSAL_USERIO_GMAIL_ACCOUNTS", "gmail,careviolan").split(",") if item.strip()}
                    if alias not in allowed:
                        raise ValueError("Gmail mailbox is not configured in himalaya")
                    account_id = f"gmail-{alias}"
                    service._store.register_account(
                        account_id=account_id, provider="gmail", display_name=alias,
                        can_read=True, can_reply=False, credential_ref=f"himalaya:{alias}", enabled=True,
                    )
                    self._reply(202, {"accepted": True, "account_id": account_id, "mode": "configured_himalaya_mailbox"})
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
            if requested_path in {"/vk/connect/new", "/vk/callback"}:
                body = _vk_connect_page(vkid_app_id).encode()
                self._html(200, body)
                return
            if requested_path == "/gmail/connect/new":
                self._html(200, _gmail_connect_page().encode())
                return
            if requested_path == "/" and self._static(requested_path):
                return
            if requested_path.startswith("/assets/") and self._static(requested_path):
                return
            if requested_path in {"/vk-userio-extension.zip", "/vk-userio-extension-mv3.zip"} and self._static(requested_path):
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

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if not self._authorized(allow_proxy=True):
                self._reply(401, {"error": "unauthorized"})
                return
            if path.startswith("/v1/accounts/"):
                account_id = unquote(path.removeprefix("/v1/accounts/").strip("/"))
                self._reply(200, {"deleted": service._store.delete_account(account_id)})
                return
            self._reply(404, {"error": "not found"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return UserIOHandler


def _vk_connect_page(app_id: str) -> str:
    safe_app_id = app_id.strip() or "0"
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect VK</title><style>body{{font:16px system-ui;max-width:560px;margin:10vh auto;padding:24px;background:#111;color:#eee}}#status{{margin-top:20px;color:#aaa}}a{{color:#8ab4f8}}</style></head>
<body><h1>Connect VK</h1><p>VK ID links the account. Reading chats is enabled separately by the VK browser extension.</p>
<div id="vk_auth"></div><p id="status">Waiting for VK ID…</p>
<script src="https://unpkg.com/@vkid/sdk@<3.0.0/dist-sdk/umd/index.js"></script>
<script>
(() => {{
  const status = document.getElementById('status');
  const VKID = window.VKIDSDK;
  if (!VKID) {{ status.textContent = 'VK ID SDK failed to load'; return; }}
  VKID.Config.init({{app:{safe_app_id}, redirectUrl:location.origin + '/vk/callback', responseMode:VKID.ConfigResponseMode.Callback, source:VKID.ConfigSource.LOWCODE, scope:''}});
  const tap = new VKID.OneTap();
  tap.render({{container:document.getElementById('vk_auth'), showAlternativeLogin:true}})
    .on(VKID.WidgetEvents.ERROR, error => {{ status.textContent = 'VK ID error'; console.error(error); }})
    .on(VKID.OneTapInternalEvents.LOGIN_SUCCESS, payload => {{
      status.textContent = 'Authorizing…';
      VKID.Auth.exchangeCode(payload.code, payload.device_id)
        .then(result => VKID.Auth.userInfo(result.access_token))
        .then(info => fetch('/v1/vk/accounts', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{user_id:String(info.user_id), display_name:info.user_name || info.first_name || 'VK account'}})}}))
        .then(response => {{ if (!response.ok) throw new Error('UserIO rejected account'); status.textContent = 'VK identity connected. Install the browser extension to read chats.'; }})
        .catch(error => {{ status.textContent = 'Could not connect VK'; console.error(error); }});
    }});
}})();
</script></body></html>'''


def _gmail_connect_page() -> str:
    allowed = [item.strip() for item in os.getenv("UNIVERSAL_USERIO_GMAIL_ACCOUNTS", "gmail,careviolan").split(",") if item.strip()]
    options = "".join(f"<option value='{item}'>{item}</option>" for item in allowed)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Gmail</title><style>body{{font:16px system-ui;max-width:560px;margin:10vh auto;padding:24px;background:#111;color:#eee}}select,button{{font:inherit;padding:10px;margin-top:12px}}#status{{margin-top:20px;color:#aaa}}a{{color:#8ab4f8}}</style></head>
<body><h1>Connect Gmail</h1><p>UserIO uses the local read-only Himalaya configuration. This page adds only a mailbox that is already configured there; it never asks for or stores a Gmail password.</p>
<label for="account">Configured mailbox</label><br><select id="account">{options}</select><br><button id="connect">Add Gmail account</button><p id="status"></p><p><a href="/">Back to UserIO</a></p>
<script>
document.getElementById('connect').onclick = async () => {{
  const status = document.getElementById('status');
  try {{ const response = await fetch('/v1/gmail/accounts', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{account:document.getElementById('account').value}})}}); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not add mailbox'); status.textContent = 'Added. Returning to UserIO…'; setTimeout(() => location.assign('/'), 400); }}
  catch (error) {{ status.textContent = error.message; }}
}};
</script></body></html>'''
