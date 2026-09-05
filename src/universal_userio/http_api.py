"""Small authenticated HTTP boundary for the UserIO business flow."""

from __future__ import annotations

import hmac
import imaplib
import json
import mimetypes
import os
import re
import subprocess
import time
from base64 import b64decode
from binascii import Error as BinasciiError
from http.cookies import CookieError, SimpleCookie
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Type
from urllib.parse import parse_qs, unquote, urlparse

from . import collect
from .adapters import inbox_message_from_envelope
from .channels.core import AdapterNotSupported
from .contracts import UserPrincipal
from .mcp_surface import UserIOMcpSurface
from .mcp_transport import json_rpc_response, sse_message
from .oauth import OAuthError, OAuthProvider
from .service import DeliveryUnavailableError, UserIOService


_STATIC_ROOT = Path(__file__).with_name("static")
_HIMALAYA_CONFIG = Path("/home/roomhacker/.config/himalaya/config.toml")
_GMAIL_SECRET_ROOT = Path("/home/roomhacker/.hermes/secrets/universal-userio-gmail")
_GMAIL_ACCOUNTS_FILE = Path("/var/lib/universal-inbox/gmail-accounts.txt")
_GMAIL_PASSWORD_HELPER = "/usr/local/bin/universal-userio-gmail-password"
_DASHBOARD_SESSION_LIFETIME = 12 * 60 * 60

# Mirrors the web client's MEDIA_PLACEHOLDER regex so the /media endpoint can
# describe a bubble without trusting the client.
_PLACEHOLDER_RE = re.compile(
    r"^\[\s*(?:WhatsApp|Telegram)?\s*"
    r"(image|video|audio|voice|document|sticker|фото|видео|аудио|голосовое|файл)"
    r"\s*\]$",
    re.IGNORECASE,
)


def handler(
    service: UserIOService, *, token: str, vkid_app_id: str = "",
    trusted_proxy_token: str = "",
) -> Type[BaseHTTPRequestHandler]:
    surface = UserIOMcpSurface(service._store, service)
    oauth = OAuthProvider(service._store)

    class UserIOHandler(BaseHTTPRequestHandler):
        def _principal(self, *, allow_proxy: bool = False) -> UserPrincipal | None:
            presented = self.headers.get("Authorization", "")
            if token and hmac.compare_digest(presented, f"Bearer {token}"):
                return service._store.owner()
            if presented.startswith("Bearer "):
                principal = service._store.authenticate_token(presented.removeprefix("Bearer ").strip())
                if principal is not None:
                    return principal
            principal = self._cookie_principal("userio_web_session")
            if principal is not None:
                return principal
            if (
                allow_proxy and trusted_proxy_token
                and self.headers.get("X-UserIO-Authenticated") == "1"
                and hmac.compare_digest(
                    self.headers.get("X-UserIO-Proxy-Token", ""), trusted_proxy_token
                )
            ):
                owner = service._store.owner()
                return UserPrincipal(owner.user_id, owner.username, owner.role)
            return None

        def _reply(self, status: int, payload: dict, headers: dict[str, str] | None = None) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def _sse(self, payload: dict) -> None:
            encoded = sse_message(payload)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _html(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _reply_raw(self, status: int, body: bytes, *, content_type: str, filename: str) -> None:
            disposition = 'attachment; filename="' + filename.replace('"', "") + '"'
            self.send_response(status)
            self.send_header("Content-Type", content_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", disposition)
            self.send_header("Cache-Control", "private, max-age=0")
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

        def _form(self) -> dict[str, str]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            return {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}

        def _base_url(self) -> str:
            scheme = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
            if scheme not in {"http", "https"}:
                scheme = "https" if self.headers.get("Forwarded", "").startswith("proto=https") else "http"
            return f"{scheme}://{self.headers.get('Host', 'localhost')}"

        def _cookie_principal(self, name: str) -> UserPrincipal | None:
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except (CookieError, ValueError):
                return None
            morsel = cookie.get(name)
            return None if morsel is None else service._store.oauth_session_user(morsel.value)

        def _oauth_session(self) -> UserPrincipal | None:
            return self._cookie_principal("userio_oauth_session")

        def _basic_client(self) -> tuple[str, str] | None:
            value = self.headers.get("Authorization", "")
            if not value.startswith("Basic "):
                return None
            try:
                decoded = b64decode(value.removeprefix("Basic "), validate=True).decode()
                client_id, secret = decoded.split(":", 1)
            except (BinasciiError, UnicodeDecodeError, ValueError):
                raise OAuthError("invalid_client", "malformed basic authentication", 401) from None
            return client_id, secret

        def _oauth_error(self, error: OAuthError) -> None:
            headers = {"WWW-Authenticate": "Basic realm=\"token\""} if error.status == 401 else None
            self._reply(error.status, {"error": error.error, "error_description": error.description}, headers)

        def _mcp_unauthorized(self) -> None:
            metadata = self._base_url() + "/.well-known/oauth-protected-resource"
            self._reply(
                401, {"error": "unauthorized"},
                {"WWW-Authenticate": f'Bearer resource_metadata="{metadata}"'},
            )

        def _redirect(self, location: str, *, cookie: str | None = None) -> None:
            self.send_response(302)
            self.send_header("Location", location)
            if cookie is not None:
                self.send_header("Set-Cookie", cookie)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _session_cookie(self, name: str, value: str, *, max_age: int, path: str) -> str:
            cookie = f"{name}={value}; Max-Age={max_age}; Path={path}; HttpOnly; SameSite=Lax"
            if self._base_url().startswith("https://"):
                cookie += "; Secure"
            return cookie

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/auth/session":
                values = self._form()
                principal = service._store.authenticate_credentials(
                    values.get("username", ""), values.get("password", "")
                )
                if principal is None:
                    self._html(401, _dashboard_login_page(invalid=True).encode())
                    return
                session = service._store.create_oauth_session(
                    principal.user_id, lifetime=_DASHBOARD_SESSION_LIFETIME
                )
                self._redirect("/", cookie=self._session_cookie(
                    "userio_web_session", session,
                    max_age=_DASHBOARD_SESSION_LIFETIME, path="/",
                ))
                return
            if path == "/auth/signup":
                values = self._form()
                password = values.get("password", "")
                if not hmac.compare_digest(password, values.get("password_confirm", "")):
                    self._html(400, _dashboard_signup_page("Пароли не совпадают.").encode())
                    return
                try:
                    principal = service._store.register_user(values.get("username", ""), password)
                except ValueError as error:
                    message = (
                        "Такой логин уже занят."
                        if "already exists" in str(error)
                        else "Логин должен содержать 3–64 безопасных символа, а пароль — минимум 8."
                    )
                    self._html(409 if "already exists" in str(error) else 400,
                               _dashboard_signup_page(message).encode())
                    return
                session = service._store.create_oauth_session(
                    principal.user_id, lifetime=_DASHBOARD_SESSION_LIFETIME
                )
                self._redirect("/", cookie=self._session_cookie(
                    "userio_web_session", session,
                    max_age=_DASHBOARD_SESSION_LIFETIME, path="/",
                ))
                return
            if path == "/auth/logout":
                self._redirect("/login", cookie=self._session_cookie(
                    "userio_web_session", "", max_age=0, path="/",
                ))
                return
            if path == "/register":
                try:
                    self._reply(201, oauth.register(self._json()))
                except (OAuthError, ValueError, json.JSONDecodeError) as error:
                    self._oauth_error(error if isinstance(error, OAuthError) else OAuthError("invalid_request", str(error)))
                return
            if path == "/token":
                try:
                    self._reply(200, oauth.token(self._form(), self._basic_client()))
                except OAuthError as error:
                    self._oauth_error(error)
                return
            if path == "/authorize":
                try:
                    location, principal = oauth.authorize(self._form(), self._oauth_session())
                    session = service._store.create_oauth_session(principal.user_id)
                    self._redirect(location, cookie=self._session_cookie(
                        "userio_oauth_session", session, max_age=600, path="/authorize",
                    ))
                except OAuthError as error:
                    self._oauth_error(error)
                return
            if path == "/auth/login":
                try:
                    payload = self._json()
                    result = service._store.login(
                        str(payload.get("username") or ""), str(payload.get("password") or "")
                    )
                except (ValueError, json.JSONDecodeError):
                    result = None
                if result is None:
                    self._reply(401, {"error": "invalid credentials"})
                    return
                principal, issued_token = result
                self._reply(200, {
                    "token": issued_token, "token_type": "Bearer",
                    "user": {"id": principal.user_id, "username": principal.username, "role": principal.role},
                })
                return
            principal = self._principal(allow_proxy=path != "/mcp")
            if principal is None:
                if path == "/mcp":
                    self._mcp_unauthorized()
                    return
                self._reply(401, {"error": "unauthorized"})
                return
            try:
                if path == "/mcp":
                    request = self._json()
                    response = json_rpc_response(surface, request, principal=principal)
                    if response is None:
                        self.send_response(202)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                    elif "text/event-stream" in self.headers.get("Accept", ""):
                        self._sse(response)
                    else:
                        self._reply(200, response)
                    return
                user_id = principal.user_id
                if path == "/v1/users":
                    if principal.role != "owner":
                        self._reply(403, {"error": "owner required"})
                        return
                    payload = self._json()
                    user, issued_token = service._store.create_user(
                        str(payload.get("username") or ""), str(payload.get("password") or "")
                    )
                    self._reply(201, {
                        "user": {"id": user.user_id, "username": user.username, "role": user.role},
                        "token": issued_token, "token_returned_once": True,
                    })
                    return
                if path == "/v1/channel-routes":
                    if principal.role != "owner":
                        self._reply(403, {"error": "owner required"})
                        return
                    payload = self._json()
                    target = service._store.user(str(payload.get("user") or ""))
                    if target is None:
                        raise ValueError("user not found")
                    service._store.bind_channel_route(
                        user_id=target.user_id, source=str(payload.get("source") or ""),
                        route_id=str(payload.get("route_id") or ""),
                    )
                    self._reply(201, {"accepted": True})
                    return
                if path == "/v1/conversations":
                    payload = self._json()
                    source = str(payload.get("source") or "").strip().lower()
                    sender = str(payload.get("sender") or "").strip()
                    if source not in {"telegram", "matrix", "whatsapp", "vk", "phone", "sms", "email", "gmail"} and not source.startswith("gmail:"):
                        raise ValueError("unsupported conversation source")
                    if not sender:
                        raise ValueError("sender is required")
                    record = service._store.create_conversation(source, sender, user_id=user_id)
                    self._reply(201, {"conversation": record})
                    return
                if path == "/v1/messages":
                    payload = self._json()
                    route_id = str(payload.get("route_id") or "")
                    if not route_id:
                        raise ValueError("route_id required")
                    message = inbox_message_from_envelope(payload.get("message") or {}, received_at=time.time())
                    target_user_id = user_id
                    if principal.service_account:
                        target_user_id = service._store.ingress_user(
                            source=message.source, account_id=str(payload.get("account_id") or "")
                        ) or user_id
                    conversation_id, accepted = service.receive(
                        message, route_id=route_id, user_id=target_user_id
                    )
                    draft = None
                    conversation = service._store.conversation(
                        conversation_id, user_id=target_user_id
                    )
                    if accepted and conversation and conversation["response_mode"] == "auto_send":
                        proposed = service.propose(
                            conversation_id, message, user_id=target_user_id
                        )
                        draft = service.approve(proposed.id, user_id=target_user_id)
                    self._reply(202, {"conversation_id": conversation_id, "accepted": accepted, "draft": None if draft is None else {"id": draft.id, "body": draft.body, "status": draft.status}})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/ai-drafts"):
                    conversation_id = path.removeprefix("/v1/conversations/").removesuffix("/ai-drafts").strip("/")
                    drafts = service.propose_from_conversation(conversation_id, user_id=user_id)
                    self._reply(202, {"drafts": [{"id": draft.id, "body": draft.body, "status": draft.status} for draft in drafts]})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/drafts"):
                    conversation_id = path.removeprefix("/v1/conversations/").removesuffix("/drafts").strip("/")
                    draft = service.create_manual_draft(
                        conversation_id, body=str(self._json().get("body") or ""), user_id=user_id
                    )
                    self._reply(202, {"id": draft.id, "body": draft.body, "status": draft.status})
                    return
                if path == "/v1/identities":
                    payload = self._json()
                    service._store.register_identity(
                        source=str(payload.get("source") or ""), external_id=str(payload.get("external_id") or ""),
                        identity_id=str(payload.get("identity_id") or ""), display_name=str(payload.get("display_name") or ""),
                        user_id=user_id,
                    )
                    self._reply(202, {"accepted": True})
                    return
                if path == "/v1/accounts":
                    payload = self._json()
                    service._store.register_account(
                        account_id=str(payload.get("id") or ""), provider=str(payload.get("provider") or ""),
                        display_name=str(payload.get("display_name") or ""), can_read=bool(payload.get("can_read")),
                        can_reply=bool(payload.get("can_reply")), credential_ref=str(payload.get("credential_ref") or ""),
                        enabled=bool(payload.get("enabled", True)), user_id=user_id,
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
                        user_id=user_id,
                    )
                    self._reply(202, {"accepted": True, "account_id": f"vk:{external_id}", "mode": "vkid_identity_only"})
                    return
                if path == "/v1/gmail/accounts":
                    payload = self._json()
                    email = str(payload.get("email") or "").strip().lower()
                    app_password = re.sub(r"\s+", "", str(payload.get("app_password") or ""))
                    account_id, display_name = _connect_gmail_account(email, app_password)
                    service._store.register_account(
                        account_id=account_id, provider="gmail", display_name=display_name,
                        can_read=True, can_reply=True, credential_ref=f"himalaya:{account_id.removeprefix('gmail-')}", enabled=True,
                        user_id=user_id,
                    )
                    self._reply(202, {"accepted": True, "account_id": account_id, "mode": "himalaya_imap_app_password"})
                    return
                if path == "/v1/reply-rules":
                    payload = self._json()
                    service._store.set_rule(
                        identity_id=str(payload.get("identity_id") or ""), source=str(payload.get("source") or ""),
                        route_id=str(payload.get("route_id") or ""), mode=str(payload.get("mode") or ""),
                        user_id=user_id,
                    )
                    self._reply(202, {"accepted": True})
                    return
                if path == "/v1/inbox/seen":
                    payload = self._json()
                    changed = service._store.mark_seen(
                        source=str(payload.get("source") or ""),
                        message_id=str(payload.get("message_id") or ""), user_id=user_id,
                    )
                    self._reply(202, {"changed": changed})
                    return
                if path == "/v1/collect/results":
                    collect.append_result(self._json(), user=principal.username)
                    self._reply(202, {"accepted": True})
                    return
                if path.startswith("/v1/drafts/") and path.endswith("/approve"):
                    draft_id = path.removeprefix("/v1/drafts/").removesuffix("/approve").strip("/")
                    draft = service.approve(draft_id, user_id=user_id)
                    self._reply(202, {"id": draft.id, "status": draft.status})
                    return
                self._reply(404, {"error": "not found"})
            except DeliveryUnavailableError as error:
                self._reply(409, {"error": str(error), "code": "delivery_unavailable"})
            except (KeyError, ValueError) as error:
                self._reply(400, {"error": str(error)})
            except RuntimeError as error:
                self._reply(502, {"error": str(error)})

        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            requested_path = urlparse(self.path).path
            if requested_path == "/.well-known/oauth-protected-resource":
                self._reply(200, oauth.protected_resource_metadata(self._base_url()))
                return
            if requested_path == "/.well-known/oauth-authorization-server":
                self._reply(200, oauth.authorization_server_metadata(self._base_url()))
                return
            if requested_path == "/authorize":
                try:
                    values = {key: value[-1] for key, value in query.items()}
                    self._html(200, oauth.authorization_form(
                        oauth.authorize_request(values), self._oauth_session()
                    ))
                except OAuthError as error:
                    self._oauth_error(error)
                return
            if requested_path == "/mcp":
                principal = self._principal()
                if principal is None:
                    self._mcp_unauthorized()
                    return
                if "text/event-stream" not in self.headers.get("Accept", ""):
                    self._reply(405, {"error": "Accept: text/event-stream required"})
                    return
                self._sse({
                    "jsonrpc": "2.0", "method": "userio/ready",
                    "params": {"endpoint": "/mcp", "username": principal.username},
                })
                return
            if requested_path == "/login":
                if self._cookie_principal("userio_web_session") is not None:
                    self._redirect("/")
                    return
                self._html(200, _dashboard_login_page().encode())
                return
            if requested_path == "/signup":
                if self._cookie_principal("userio_web_session") is not None:
                    self._redirect("/")
                    return
                self._html(200, _dashboard_signup_page().encode())
                return
            if requested_path.startswith("/assets/") and self._static(requested_path):
                return
            if requested_path == "/download":
                self._html(200, _download_page().encode())
                return
            if requested_path in {"/vk-userio-extension.zip", "/vk-userio-extension-mv3.zip",
                                  "/chatgpt-cdp-setup.zip"} and self._static(requested_path):
                return
            if requested_path in {"/vk/connect/new", "/vk/callback"}:
                if self._principal(allow_proxy=True) is None:
                    self._redirect("/login")
                    return
                body = _vk_connect_page(vkid_app_id).encode()
                self._html(200, body)
                return
            if requested_path == "/gmail/connect/new":
                if self._principal(allow_proxy=True) is None:
                    self._redirect("/login")
                    return
                self._html(200, _gmail_connect_page().encode())
                return
            if requested_path == "/":
                if self._principal(allow_proxy=True) is None:
                    self._redirect("/login")
                    return
                if self._static(requested_path):
                    return
            principal = self._principal(allow_proxy=True)
            if principal is None:
                self._reply(401, {"error": "unauthorized"})
                return
            path = urlparse(self.path).path
            user_id = principal.user_id
            if path == "/v1/inbox":
                self._reply(200, {"messages": service._store.new_messages(user_id=user_id)})
                return
            if path == "/v1/accounts":
                self._reply(200, {"accounts": service._store.accounts(user_id=user_id)})
                return
            if path == "/v1/conversations":
                source = query.get("source", [""])[0].strip().lower() or None
                conversations = service._store.conversations(source=source, user_id=user_id)
                # If the latest message in a conversation is an attachment
                # placeholder ([image], [document] …) replace it with the most
                # recent text body so the chat list and the search box always
                # point at real content the operator can act on.
                text_overrides = service._store.last_text_bodies_for(
                    [str(c["id"]) for c in conversations], user_id=user_id,
                )
                for entry in conversations:
                    preview = str(entry.get("preview") or "")
                    if preview.startswith("[") and preview.endswith("]"):
                        replacement = text_overrides.get(str(entry["id"]))
                        if replacement:
                            entry["preview"] = replacement
                self._reply(200, {"conversations": conversations})
                return
            if path == "/v1/collect/tasks":
                try:
                    self._reply(200, collect.active_tasks())
                except (ValueError, json.JSONDecodeError) as error:
                    self._reply(500, {"error": f"collect tasks: {error}"})
                return
            if path == "/v1/collect/results":
                try:
                    self._reply(200, collect.read_results(
                        task_id=(query.get("task_id", [""])[0].strip() or None),
                        limit=query.get("limit", ["50"])[0],
                    ))
                except ValueError as error:
                    self._reply(400, {"error": str(error)})
                return
            conversation_id = path.removeprefix("/v1/conversations/")
            if not conversation_id or conversation_id == self.path:
                self._reply(404, {"error": "not found"})
                return
            # /v1/conversations/{id}/media/{message_id} -> describe one
            # message's media: its placeholder kind, source channel, and the
            # bare minimum the chat bubble needs to render a clickable
            # preview. Real bytes flow only when an adapter's
            # StoredChannelAdapter.download implementation actually returns
            # them; today every adapter raises AdapterNotSupported, so the
            # honest answer for any platform is `available: false`.
            if conversation_id.endswith("/raw"):
                real_id, _, message_id = conversation_id.removesuffix("/raw").rpartition("/media/")
                if not real_id or not message_id:
                    self._reply(404, {"error": "raw path requires /v1/conversations/{id}/media/{message_id}/raw"})
                    return
                conv = service._store.conversation(real_id, user_id=user_id)
                if conv is None:
                    self._reply(404, {"error": "conversation not found"})
                    return
                source_hint = str(conv.get("source") or "")
                message = service._store.message(message_id, source=source_hint, user_id=user_id)
                if message is None or str(message.get("conversation_id") or "") != real_id:
                    self._reply(404, {"error": "message not found"})
                    return
                adapter = _adapter_for_message(service, message, user_id)
                # VK adapter needs attachment_id; everything else keys on message_id.
                file_ref = message_id
                if str(message.get("source") or "") == "vk":
                    attachments = service._store.attachments_for_message(
                        source=str(message.get("source") or ""),
                        message_id=str(message.get("message_id") or ""),
                        user_id=user_id,
                    )
                    if attachments:
                        file_ref = str(attachments[0].get("attachment_id") or message_id)
                try:
                    file = adapter.download(file_ref=file_ref)
                except KeyError:
                    self._reply(404, {"error": "message not found"})
                    return
                except (AdapterNotSupported, ValueError) as error:
                    self._reply(503, {"error": str(error)})
                    return
                self._reply_raw(200, file.data, content_type=file.content_type,
                                filename=file.filename)
                return
            if "/media/" in conversation_id:
                real_id, _, message_id = conversation_id.rpartition("/media/")
                if not real_id or not message_id or message_id == conversation_id:
                    self._reply(404, {"error": "media path requires /v1/conversations/{id}/media/{message_id}"})
                    return
                conv = service._store.conversation(real_id, user_id=user_id)
                if conv is None:
                    self._reply(404, {"error": "conversation not found"})
                    return
                source_hint = str(conv.get("source") or "")
                message = service._store.message(message_id, source=source_hint, user_id=user_id)
                if message is None or str(message.get("conversation_id") or "") != real_id:
                    self._reply(404, {"error": "message not found"})
                    return
                placeholder = _PLACEHOLDER_RE.match(str(message.get("body") or "").strip())
                # VK stores its media in the extension's IndexedDB; expose the
                # attachment rows so the chat bubble can show filenames even
                # before any byte has been pulled.
                attachments = service._store.attachments_for_message(
                    source=source_hint, message_id=str(message.get("message_id") or ""),
                    user_id=user_id,
                )
                payload = {
                    "conversation_id": real_id,
                    "message_id": str(message.get("message_id") or ""),
                    "source": str(message.get("source") or ""),
                    "received_at": message.get("received_at"),
                    "kind": placeholder.group(1).lower() if placeholder else None,
                    "available": False,
                    "reason": "media download is not connected for this account yet",
                    "attachments": [
                        {
                            "idx": a.get("idx"),
                            "kind": a.get("kind"),
                            "content_type": a.get("content_type"),
                            "filename": a.get("filename"),
                            "size": a.get("size"),
                            "attachment_id": a.get("attachment_id"),
                        }
                        for a in attachments
                    ],
                }
                adapter = _adapter_for_message(service, message, user_id)
                # VK adapter needs the FIRST attachment_id, not message_id;
                # every other channel still keys on message_id which we
                # already pass through.
                file_ref = message_id
                if str(message.get("source") or "") == "vk" and attachments:
                    file_ref = str(attachments[0].get("attachment_id") or message_id)
                try:
                    file = adapter.download(file_ref=file_ref)
                except (AdapterNotSupported, KeyError, ValueError) as error:
                    payload["reason"] = str(error)
                else:
                    payload["available"] = True
                    payload["reason"] = ""
                    payload["filename"] = file.filename
                    payload["content_type"] = file.content_type
                    payload["size"] = len(file.data)
                    payload["download_url"] = f"/v1/conversations/{real_id}/media/{message_id}/raw"
                self._reply(200, payload)
                return
            record = service._store.conversation(conversation_id, user_id=user_id)
            self._reply(200 if record else 404, record or {"error": "conversation not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            principal = self._principal(allow_proxy=True)
            if principal is None:
                self._reply(401, {"error": "unauthorized"})
                return
            if path.startswith("/v1/accounts/"):
                account_id = unquote(path.removeprefix("/v1/accounts/").strip("/"))
                self._reply(200, {
                    "deleted": service._store.delete_account(account_id, user_id=principal.user_id)
                })
                return
            self._reply(404, {"error": "not found"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return UserIOHandler


def _adapter_for_message(service, message: dict[str, object], user_id: str):
    """Pick the StoredChannelAdapter that owns this message's source."""
    from .adapters import (
        MailChannelAdapter,
        TelegramChannelAdapter,
        WhatsAppChannelAdapter,
        VKChannelAdapter,
        AndroidSmsChannelAdapter,
    )
    source = str(message.get("source") or "")
    table = {
        "mail": MailChannelAdapter,
        "email": MailChannelAdapter,
        "gmail": MailChannelAdapter,
        "telegram": TelegramChannelAdapter,
        "whatsapp": WhatsAppChannelAdapter,
        "vk": VKChannelAdapter,
        "sms": AndroidSmsChannelAdapter,
    }
    key = source.split(":", 1)[0] if ":" in source else source
    adapter_type = table.get(key)
    if adapter_type is None:
        raise ValueError(f"unsupported channel for media: {source!r}")
    return adapter_type(service._store, service, user_id)


def _download_page() -> str:
    return """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Downloads — Universal UserIO</title>
<style>
  :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
  body { min-height:100vh; margin:0; background:#0b1020; color:#edf2ff; }
  main { max-width: 720px; margin: 8vh auto; padding: 0 24px 48px; }
  h1 { font-size: 26px; margin: 0 0 6px; }
  p.lead { color:#aebbd7; margin-top: 0; }
  .card { border:1px solid #2b3554; border-radius:16px; background:#121a2e; padding:22px 24px; margin-top:18px; }
  .card h2 { margin:0 0 8px; font-size:19px; }
  .card p { color:#aebbd7; margin:6px 0; font-size:14px; }
  .btn { display:inline-block; margin-top:12px; margin-right:10px; padding:10px 18px; border-radius:9px; background:#6d7cff; color:white; font-weight:700; text-decoration:none; }
  .btn.secondary { background:#2b3554; }
  code { background:#0b1020; border:1px solid #2b3554; border-radius:6px; padding:2px 7px; font-size:13px; }
  ol { color:#aebbd7; font-size:14px; padding-left: 20px; }
  .tag { display:inline-block; font-size:11px; border-radius:999px; padding:2px 10px; margin-left:8px; background:#24345c; color:#9eabff; vertical-align: middle; }
</style>
</head>
<body>
<main>
  <h1>Universal UserIO — загрузки</h1>
  <p class="lead">Скачайте коннектор, распакуйте и загрузите как unpacked-расширение в Chrome / Chromium / BrowserOS / Brave.</p>

  <div class="card">
    <h2>Universal UserIO Agent <span class="tag">браузерное расширение</span></h2>
    <p>Захват VK Web (чаты, поиск, отправка) + универсальный агент сбора данных с сайтов через пользовательские сессии. Никаких куки и токенов сайтов наружу — только ваш настроенный UserIO.</p>
    <a class="btn" href="/vk-userio-extension-mv3.zip">Скачать для Chrome / Chromium (MV3)</a>
    <a class="btn secondary" href="/vk-userio-extension.zip">Legacy MV2</a>
    <ol>
      <li>Распакуйте zip в постоянную папку.</li>
      <li>Откройте <code>chrome://extensions</code>, включите Developer mode → <b>Load unpacked</b> → выберите папку.</li>
      <li>В настройках расширения укажите endpoint <code>https://msg.bezrabotnyi.com</code> и свой UserIO API token.</li>
    </ol>
  </div>

  <div class="card">
    <h2>ChatGPT CDP starter <span class="tag">экспериментально</span></h2>
    <p>Серверный пакет <code>chatgpt-cdp-mcp</code>: превращает одну залогиненную страницу ChatGPT в ограниченный MCP-инструмент для UserIO. Требует Node.js 20+ и CDP-драйвер к вашему браузеру (см. README внутри пакета).</p>
    <a class="btn" href="/chatgpt-cdp-setup.zip">Скачать setup-пакет</a>
    <a class="btn secondary" href="https://github.com/megamen32/chatgpt-cdp-mcp">GitHub</a>
  </div>
</main>
</body>
</html>"""


def _dashboard_login_page(*, invalid: bool = False) -> str:
    error = (
        '<p class="error" role="alert">Неверный логин или пароль.</p>' if invalid else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Вход — Universal UserIO</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ min-height:100vh; margin:0; display:grid; place-items:center; background:#0b1020; color:#edf2ff; }}
    main {{ width:min(360px, calc(100vw - 48px)); padding:32px; border:1px solid #2b3554; border-radius:18px; background:#121a2e; box-shadow:0 24px 70px #0008; }}
    h1 {{ margin:0 0 8px; font-size:24px; }}
    p {{ color:#aebbd7; }}
    label {{ display:block; margin-top:18px; font-size:14px; }}
    input {{ box-sizing:border-box; width:100%; margin-top:7px; padding:11px 12px; border:1px solid #3a4769; border-radius:9px; background:#0b1020; color:inherit; font:inherit; }}
    button {{ width:100%; margin-top:22px; padding:11px; border:0; border-radius:9px; background:#6d7cff; color:white; font:inherit; font-weight:700; cursor:pointer; }}
    .error {{ padding:10px 12px; border-radius:8px; background:#5b1f2a; color:#ffd9df; }}
  </style>
</head>
<body>
  <main>
    <h1>Universal UserIO</h1>
    <p>Войдите, чтобы открыть свои каналы и аккаунты.</p>
    {error}
    <form method="post" action="/auth/session">
      <label>Логин<input name="username" autocomplete="username" required autofocus></label>
      <label>Пароль<input name="password" type="password" autocomplete="current-password" required></label>
      <button type="submit">Войти</button>
    </form>
    <p>Нет аккаунта? <a href="/signup">Зарегистрироваться</a></p>
  </main>
</body>
</html>"""


def _dashboard_signup_page(error: str = "") -> str:
    error_html = f'<p class="error" role="alert">{error}</p>' if error else ""
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Регистрация — Universal UserIO</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ min-height:100vh; margin:0; display:grid; place-items:center; background:#0b1020; color:#edf2ff; }}
    main {{ width:min(360px, calc(100vw - 48px)); padding:32px; border:1px solid #2b3554; border-radius:18px; background:#121a2e; box-shadow:0 24px 70px #0008; }}
    h1 {{ margin:0 0 8px; font-size:24px; }}
    p {{ color:#aebbd7; }} a {{ color:#9eabff; }}
    label {{ display:block; margin-top:18px; font-size:14px; }}
    input {{ box-sizing:border-box; width:100%; margin-top:7px; padding:11px 12px; border:1px solid #3a4769; border-radius:9px; background:#0b1020; color:inherit; font:inherit; }}
    button {{ width:100%; margin-top:22px; padding:11px; border:0; border-radius:9px; background:#6d7cff; color:white; font:inherit; font-weight:700; cursor:pointer; }}
    .error {{ padding:10px 12px; border-radius:8px; background:#5b1f2a; color:#ffd9df; }}
  </style>
</head>
<body>
  <main>
    <h1>Создать аккаунт</h1>
    <p>Ваши каналы и сообщения будут изолированы от других пользователей.</p>
    {error_html}
    <form method="post" action="/auth/signup">
      <label>Логин<input name="username" autocomplete="username" minlength="3" maxlength="64" required autofocus></label>
      <label>Пароль<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>
      <label>Повторите пароль<input name="password_confirm" type="password" autocomplete="new-password" minlength="8" required></label>
      <button type="submit">Зарегистрироваться</button>
    </form>
    <p>Уже есть аккаунт? <a href="/login">Войти</a></p>
  </main>
</body>
</html>"""


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
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Gmail</title><style>body{{font:16px system-ui;max-width:560px;margin:10vh auto;padding:24px;background:#111;color:#eee}}input,button{{box-sizing:border-box;width:100%;font:inherit;padding:10px;margin-top:12px}}#status{{margin-top:20px;color:#aaa}}a{{color:#8ab4f8}}small{{color:#aaa}}</style></head>
<body><h1>Connect Gmail</h1><p>Use a Google App Password for this mailbox. The normal Gmail password is never accepted.</p><small>The connection is checked against Gmail IMAP over TLS, then the App Password is stored in the local secret directory and never returned to the browser.</small>
<form id="connect"><input id="email" type="email" autocomplete="username" placeholder="you@gmail.com" required><input id="app_password" type="password" autocomplete="new-password" placeholder="16-character App Password" minlength="16" required><button type="submit">Add Gmail account</button></form><p id="status"></p><p><a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer">Create an App Password</a> · <a href="/">Back to UserIO</a></p>
<script>
document.getElementById('connect').onsubmit = async (event) => {{
  event.preventDefault();
  const status = document.getElementById('status');
  try {{ const response = await fetch('/v1/gmail/accounts', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify({{email:document.getElementById('email').value, app_password:document.getElementById('app_password').value}})}}); const data = await response.json(); if (!response.ok) throw new Error(data.error || 'Could not add mailbox'); document.getElementById('app_password').value = ''; status.textContent = 'Added and verified. Returning to UserIO…'; setTimeout(() => location.assign('/'), 700); }}
  catch (error) {{ status.textContent = error.message; }}
}};
</script></body></html>'''


def _connect_gmail_account(email: str, app_password: str) -> tuple[str, str]:
    """Verify and install one Gmail IMAP account without exposing its secret."""
    parsed = parseaddr(email)[1]
    if parsed != email or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@gmail\.com", email) or not re.fullmatch(r"[A-Za-z0-9]{16}", app_password):
        raise ValueError("Gmail address and App Password are invalid")
    alias = "gmail_" + re.sub(r"[^a-z0-9_-]", "_", email.replace("@", "_at_").lower())
    try:
        connection = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=20)
        connection.login(email, app_password)
        connection.logout()
    except (OSError, imaplib.IMAP4.error) as error:
        raise ValueError("Gmail rejected the App Password") from error
    _GMAIL_SECRET_ROOT.mkdir(parents=True, exist_ok=True)
    secret_path = _GMAIL_SECRET_ROOT / alias
    secret_path.write_text(app_password + "\n", encoding="utf-8")
    secret_path.chmod(0o600)
    try:
        import pwd
        os.chown(secret_path, pwd.getpwnam("roomhacker").pw_uid, pwd.getpwnam("roomhacker").pw_gid)
    except (KeyError, PermissionError):
        pass
    _append_himalaya_account(alias, email)
    _append_gmail_account(alias)
    subprocess.run(["systemctl", "restart", "universal-inbox-gmail.service"], check=True, timeout=30)
    return f"gmail-{alias}", email


def _append_himalaya_account(alias: str, email: str) -> None:
    current = _HIMALAYA_CONFIG.read_text(encoding="utf-8") if _HIMALAYA_CONFIG.exists() else ""
    if f"[accounts.{alias}]" in current:
        return
    block = f'''\n[accounts.{alias}]\nimap.server = "imaps://imap.gmail.com:993"\nimap.sasl.plain.username = "{email}"\nimap.sasl.plain.password.command = ["{_GMAIL_PASSWORD_HELPER}", "{alias}"]\nsmtp.server = "smtp://smtp.gmail.com:587"\nsmtp.starttls = true\nsmtp.sasl.plain.username = "{email}"\nsmtp.sasl.plain.password.command = ["{_GMAIL_PASSWORD_HELPER}", "{alias}"]\nmailbox.alias.inbox = "INBOX"\nmailbox.alias.sent = "[Gmail]/Sent Mail"\nmailbox.alias.drafts = "[Gmail]/Drafts"\nmailbox.alias.trash = "[Gmail]/Trash"\n'''
    _HIMALAYA_CONFIG.write_text(current.rstrip() + "\n" + block, encoding="utf-8")
    _HIMALAYA_CONFIG.chmod(0o600)


def _append_gmail_account(alias: str) -> None:
    _GMAIL_ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = _GMAIL_ACCOUNTS_FILE.read_text(encoding="utf-8").split() if _GMAIL_ACCOUNTS_FILE.exists() else ["gmail", "careviolan"]
    if alias not in existing:
        existing.append(alias)
    _GMAIL_ACCOUNTS_FILE.write_text("\n".join(dict.fromkeys(existing)) + "\n", encoding="utf-8")
    _GMAIL_ACCOUNTS_FILE.chmod(0o640)
