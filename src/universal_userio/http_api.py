"""Small authenticated HTTP boundary for the UserIO business flow."""

from __future__ import annotations

import hmac
import json
import time
from http.server import BaseHTTPRequestHandler
from typing import Type
from urllib.parse import urlparse

from .adapters import inbox_message_from_envelope
from .mcp_surface import UserIOMcpSurface
from .service import UserIOService


_DASHBOARD = """<!doctype html><meta charset=utf-8><title>Universal UserIO</title>
<style>body{font:16px system-ui;max-width:900px;margin:2rem auto}article{border:1px solid #ddd;padding:1rem;margin:.7rem 0}button{margin:.2rem}small{color:#666}</style>
<h1>Universal UserIO</h1><p id=status>Loading unified inbox...</p><main id=inbox></main>
<script>
const token=prompt('UserIO API token'); const api=(path, options={})=>fetch(path,{...options,headers:{...(options.headers||{}),Authorization:'Bearer '+token}});
const esc=value=>String(value).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
async function approve(id){await api('/v1/drafts/'+id+'/approve',{method:'POST',body:'{}'}); await load()}
async function seen(source,id){await api('/v1/inbox/seen',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source,message_id:id})}); await load()}
async function load(){const r=await api('/v1/inbox'); if(!r.ok){document.querySelector('#status').textContent='Authentication failed';return} const data=await r.json();
document.querySelector('#status').textContent=`${data.messages.length} new message(s)`; const root=document.querySelector('#inbox');root.innerHTML='';
for(const m of data.messages){const c=await (await api('/v1/conversations/'+m.conversation_id)).json(); const drafts=(c.drafts||[]).map(d=>`<li>${esc(d.body)} <small>${esc(d.status)}</small>${d.status==='proposed'?` <button onclick="approve('${d.id}')">Approve & send</button>`:''}</li>`).join(''); const node=document.createElement('article'); node.innerHTML=`<b>${esc(m.source)} · ${esc(m.sender)}</b><p>${esc(m.body)}</p><small>${esc(c.identity_id||'unmapped contact')}</small><ul>${drafts}</ul><button onclick="seen('${m.source}','${m.message_id}')">Mark seen</button>`;root.append(node)}} load();
</script>""".encode()


def handler(service: UserIOService, *, token: str) -> Type[BaseHTTPRequestHandler]:
    surface = UserIOMcpSurface(service._store, service)
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

        def _html(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
                    conversation_id, accepted, draft = service.receive_and_plan(message, route_id=route_id)
                    self._reply(202, {"conversation_id": conversation_id, "accepted": accepted, "draft": None if draft is None else {"id": draft.id, "body": draft.body, "status": draft.status}})
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
            if urlparse(self.path).path == "/":
                self._html(200, _DASHBOARD)
                return
            if not self._authorized():
                self._reply(401, {"error": "unauthorized"})
                return
            path = urlparse(self.path).path
            if path == "/v1/inbox":
                self._reply(200, {"messages": service._store.new_messages()})
                return
            if path == "/v1/accounts":
                self._reply(200, {"accounts": service._store.accounts()})
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
