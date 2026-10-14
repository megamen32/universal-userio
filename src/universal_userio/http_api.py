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
from urllib.parse import parse_qs, quote, unquote, urlparse
import urllib.error
import urllib.request
from collections.abc import Mapping

from . import agent_channel, chatgpt_sessions, collect, vault
from . import call_audio
from .account_connections import (
    connect_matrix, connect_imap, connect_vk_community, connect_vk_personal,
)
from .adapters import inbox_message_from_envelope
from .channels.core import AdapterNotSupported
from .contracts import UserPrincipal
from .mcp_surface import UserIOMcpSurface
from .mcp_transport import ResourceSubscriptionHub, json_rpc_response, sse_message
from .oauth import OAuthError, OAuthProvider
from .personal_tokens import PersonalTokens
from .token_permissions import read_only_http_allowed, meeting_recorder_http_allowed
from .search import search_conversations
from .service import DeliveryUnavailableError, UserIOService


_STATIC_ROOT = Path(__file__).with_name("static")
_HIMALAYA_CONFIG = Path("/home/roomhacker/.config/himalaya/config.toml")
_GMAIL_SECRET_ROOT = Path("/home/roomhacker/.hermes/secrets/universal-userio-gmail")
_GMAIL_ACCOUNTS_FILE = Path("/var/lib/universal-userio/gmail-accounts.txt")
_GMAIL_PASSWORD_HELPER = "/usr/local/bin/universal-userio-gmail-password"
_DASHBOARD_SESSION_LIFETIME = 12 * 60 * 60


def _context_preferences(service: UserIOService, user_id: str) -> dict[str, object]:
    settings = dict(service._store.context_settings(user_id=user_id))
    settings["models"] = {
        "text": os.environ.get("USERIO_AI_MODEL", "MiniMax-M2.7").strip()
        or "MiniMax-M2.7",
        "image": os.environ.get("USERIO_AI_IMAGE_MODEL", "MiniMax-M3").strip()
        or "MiniMax-M3",
    }
    return settings

# Mirrors the web client's MEDIA_PLACEHOLDER regex so the /media endpoint can
# describe a bubble without trusting the client.
_PLACEHOLDER_RE = re.compile(
    r"^\[\s*(?:WhatsApp|Telegram)?\s*"
    r"(image|video|audio|voice|document|sticker|фото|видео|аудио|голосовое|файл)"
    r"\s*\]$",
    re.IGNORECASE,
)


def _public_account_source(account: Mapping[str, object], credential_ref: str) -> str:
    provider = str(account.get("provider") or "")
    account_id = str(account.get("id") or "")
    if provider == "gmail":
        alias = credential_ref.removeprefix("himalaya:") if credential_ref.startswith("himalaya:") else ""
        if not alias:
            match = re.fullmatch(r"gmail-(.+)", account_id, re.IGNORECASE)
            alias = match.group(1) if match else ""
        return "gmail" if alias == "gmail" else f"gmail:{alias}" if alias else provider
    if provider in {"matrix", "email"} and account_id.startswith(provider + ":"):
        return account_id
    if provider == "chatgpt" and account_id.startswith("chatgpt:"):
        return account_id
    return provider


def handler(
    service: UserIOService, *, token: str, vkid_app_id: str = "",
    trusted_proxy_token: str = "", runtime_identity: Mapping[str, object] | None = None,
) -> Type[BaseHTTPRequestHandler]:
    surface = UserIOMcpSurface(service._store, service)
    oauth = OAuthProvider(service._store)
    personal_tokens = PersonalTokens(service._store)
    subscriptions = ResourceSubscriptionHub()
    from .max_accounts import MaxAccounts
    max_accounts = MaxAccounts(service)
    supplied_identity = runtime_identity or {}
    public_runtime_identity = {
        "schema_version": supplied_identity.get("schema_version", 1),
        "commit": supplied_identity.get("commit"),
        "manifest_sha256": supplied_identity.get("manifest_sha256"),
        "verified": supplied_identity.get("verified", False),
    }
    service.add_inbound_listener(lambda user_id, _conversation_id, _message: subscriptions.publish(user_id, "userio://inbox/unread"))

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
            # Use the account currently selected in the cabinet, including
            # after switching users; a short-lived prior consent is fallback.
            return self._cookie_principal("userio_web_session") or self._cookie_principal("userio_oauth_session")

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
            restricted = self._principal()
            if restricted is not None and restricted.meeting_recorder and not meeting_recorder_http_allowed(self.command, urlparse(self.path).path):
                self._reply(403, {'error': 'meeting_recording_scope_only'})
                return
            if restricted is not None and restricted.messenger_accounts is not None and path!='/mcp':
                self._reply(403, {'error':'messenger_scope_only'})
                return
            if restricted is not None and restricted.read_only and not read_only_http_allowed(self.command, path):
                self._reply(403, {"error": "read_only_token"})
                return
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
            matrix_notice_token = os.environ.get('USERIO_MATRIX_NOTICE_TOKEN','')
            if (principal is None and path in {'/v1/calls/matrix/noticeplace', '/v1/calls/telegram/noticeplace'} and matrix_notice_token
                    and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+matrix_notice_token)):
                principal = service._store.owner()
            if principal is None:
                if path == "/mcp":
                    self._mcp_unauthorized()
                    return
                self._reply(401, {"error": "unauthorized"})
                return
            try:
                if path == "/mcp":
                    # Bounded new-file staging uses at most 2MiB of bytes
                    # plus base64/JSON overhead; reject before reading a body.
                    if not 0<int(self.headers.get('Content-Length','0'))<=3_000_000:
                        self._reply(413,{'error':'mcp_body_exceeds_limit'})
                        self.close_connection=True
                        return
                    request = self._json()
                    response = json_rpc_response(surface, request, principal=principal, subscription_hub=subscriptions)
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
                if path == "/v1/personal-tokens":
                    if principal.service_account:
                        self._reply(403, {"error": "personal login required"})
                        return
                    payload = self._json()
                    self._reply(201, personal_tokens.create(user_id, payload.get("name", ""),
                                                          scope=payload.get("scope", "full_access"),
                                                          account_refs=payload.get('account_refs')),
                                headers={"Cache-Control": "no-store"})
                    return
                if path.startswith("/v1/workspace/secretary/deep-jobs/") and path.endswith("/progress"):
                    if not principal.service_account:
                        self._reply(403, {"error": "service account required"})
                        return
                    job_id = unquote(
                        path.removeprefix("/v1/workspace/secretary/deep-jobs/")
                        .removesuffix("/progress").strip("/")
                    )
                    payload = self._json()
                    result = service._store.update_conversation_secretary_job(
                        job_id=job_id, phase=str(payload.get("phase") or ""),
                        worker_id=str(payload.get("worker_id") or ""),
                        session_id=str(payload.get("session_id") or ""),
                        session_url=str(payload.get("session_url") or ""),
                        result=str(payload.get("result") or ""),
                        error=str(payload.get("error") or ""), user_id=user_id,
                    )
                    self._reply(200, {"ok": True, "job": result})
                    return
                if path == "/v1/workspace/triage" or path.startswith("/v1/workspace/triage/"):
                    if not principal.service_account:
                        self._reply(403, {"error": "service account required"})
                        return
                    payload = self._json()
                    if path == "/v1/workspace/triage":
                        if not service._store.capability_enabled("read", user_id=user_id):
                            self._reply(403, {"error": "read_capability_disabled"})
                            return
                        result = service.triage_workspace_event(
                            event_seq=payload.get("event_seq"),
                            request_id=str(payload.get("request_id") or ""),
                            max_drafts=payload.get("max_drafts", 2), user_id=user_id,
                        )
                        self._reply(200, {"ok": True, **result})
                        return
                    suffix = path.removeprefix("/v1/workspace/triage/").strip("/")
                    parts = suffix.split("/")
                    if len(parts) != 2 or parts[1] not in {"send", "deep", "feedback"}:
                        self._reply(404, {"error": "not found"})
                        return
                    event_seq = int(parts[0])
                    request_id = str(payload.get("request_id") or "")
                    actor = str(payload.get("actor") or "")
                    if parts[1] == "send":
                        if not service._store.capability_enabled("send", user_id=user_id):
                            self._reply(403, {"error": "send_capability_disabled"})
                            return
                        result = service.send_workspace_triage(
                            event_seq=event_seq, request_id=request_id,
                            draft_id=str(payload.get("draft_id") or ""), actor=actor,
                            confirm=payload.get("confirm"), user_id=user_id,
                        )
                    elif parts[1] == "deep":
                        result = service.deep_workspace_triage(
                            event_seq=event_seq, request_id=request_id, actor=actor,
                            user_id=user_id,
                        )
                    else:
                        if not service._store.capability_enabled("send", user_id=user_id):
                            self._reply(403, {"error": "send_capability_disabled"})
                            return
                        result = service.feedback_workspace_triage(
                            event_seq=event_seq, request_id=request_id,
                            label=str(payload.get("label") or ""), actor=actor,
                            user_id=user_id,
                        )
                    self._reply(200, {"ok": True, **result})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/secretary/triage"):
                    if principal.role != "owner":
                        self._reply(403, {"error": "owner required"})
                        return
                    if not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "read_capability_disabled"})
                        return
                    conversation_id = unquote(
                        path.removeprefix("/v1/conversations/")
                        .removesuffix("/secretary/triage").strip("/")
                    )
                    result = service.triage_conversation_secretary(
                        conversation_id=conversation_id,
                        actor=f"userio-web:{principal.username}", user_id=user_id,
                    )
                    self._reply(200, {"ok": True, **result})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/secretary/deep"):
                    if principal.role != "owner":
                        self._reply(403, {"error": "owner required"})
                        return
                    if not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "read_capability_disabled"})
                        return
                    conversation_id = unquote(
                        path.removeprefix("/v1/conversations/")
                        .removesuffix("/secretary/deep").strip("/")
                    )
                    payload = self._json()
                    if set(payload) - {"retry_of_job_id"}:
                        raise ValueError("secretary deep accepts only retry_of_job_id")
                    result = service.request_conversation_secretary_deep(
                        conversation_id=conversation_id,
                        actor=f"userio-web:{principal.username}", user_id=user_id,
                        retry_of_job_id=str(payload.get("retry_of_job_id") or ""),
                    )
                    self._reply(202, {"ok": True, **result})
                    return
                if path == "/v1/workspace/policy/evaluate":
                    payload = self._json()
                    account_id = str(payload.get("account_id") or "").strip()
                    if principal.service_account:
                        user_id = service._store.ingress_user(
                            source=str(payload.get("source") or ""), account_id=account_id,
                        ) or user_id
                    if not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "read_capability_disabled"})
                        return
                    conversation_id = str(payload.get("conversation_id") or "")
                    chat = service._store.evaluate_workspace_event(
                        conversation_id=conversation_id,
                        source=str(payload.get("source") or ""),
                        message_id=str(payload.get("message_id") or ""), user_id=user_id,
                    )
                    if account_id != chat["account_ref"] or str(payload.get("peer_id") or "") != chat["peer_id"]:
                        self._reply(403, {"error": "account_or_peer_mismatch"})
                        return
                    self._reply(200, {"allowed": chat["allowed"],
                        "eligible_at_ingest": chat["eligible_at_ingest"],
                        "policy_revision": chat["policy_revision"]})
                    return
                if path.startswith("/v1/conversations/") and path.endswith("/analysis-policy"):
                    if not service._store.capability_enabled("send", user_id=user_id):
                        self._reply(403, {"error": "send_capability_disabled"})
                        return
                    payload = self._json()
                    if set(payload) != {"enabled"} or not isinstance(payload["enabled"], bool):
                        raise ValueError("analysis policy requires an enabled boolean")
                    conversation_id = unquote(path.removeprefix("/v1/conversations/").removesuffix("/analysis-policy").strip("/"))
                    chat = service._store.set_workspace_chat_rule(
                        conversation_id=conversation_id,
                        action="allow" if payload["enabled"] else "ignore",
                        reason="Владелец изменил настройку «Разбирать секретарём» в UserIO.",
                        user_id=user_id,
                    )
                    self._reply(200, {"analysis_policy": chat})
                    return
                if path == "/v1/workspace/claims":
                    if not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "read_capability_disabled"})
                        return
                    payload = self._json()
                    claimed = service._store.claim_workspace_event(
                        worker_id=payload.get("worker_id"),
                        lease_seconds=payload.get("lease_seconds", 600),
                        after=payload.get("after", 0),
                        telegram_direct_only=payload.get("telegram_direct_only", False),
                        user_id=user_id,
                    )
                    self._reply(200, {"claimed": claimed is not None, **(claimed or {})})
                    return
                if path.startswith("/v1/workspace/claims/"):
                    if not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "read_capability_disabled"})
                        return
                    parts = path.removeprefix("/v1/workspace/claims/").strip("/").split("/")
                    if len(parts) != 2 or parts[1] not in {"renew", "complete", "fail"}:
                        self._reply(404, {"error": "not found"})
                        return
                    payload = self._json()
                    kwargs = {
                        "event_seq": int(parts[0]),
                        "worker_id": payload.get("worker_id"),
                        "lease_token": payload.get("lease_token"),
                        "user_id": user_id,
                    }
                    if parts[1] == "renew":
                        claim = service._store.renew_workspace_claim(
                            **kwargs, lease_seconds=payload.get("lease_seconds", 600)
                        )
                    elif parts[1] == "complete":
                        claim = service._store.complete_workspace_claim(
                            **kwargs, detail=payload.get("detail")
                        )
                    else:
                        claim = service._store.fail_workspace_claim(
                            **kwargs, detail=payload.get("detail")
                        )
                    self._reply(200, {"ok": True, "claim": claim})
                    return
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
                if path == "/v1/telegram/relay-sync":
                    if not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "read_capability_disabled"})
                        return
                    if self._json():
                        raise ValueError("relay-sync takes no caller-selected account or peer")
                    self._reply(200, {"ok": True, **service.sync_relay_account(user_id=user_id)})
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
                if path in {"/v1/diagnostic/chats", "/v1/diagnostic/messages"}:
                    from .diagnostic_chats import dispatch
                    operation = 'create' if path.endswith('/chats') else 'message'
                    self._reply(200, dispatch(service._store, principal, 'userio.diagnostic.'+operation, self._json()))
                    return
                if path == "/v1/conversations":
                    payload = self._json()
                    source = str(payload.get("source") or "").strip().lower()
                    sender = str(payload.get("sender") or "").strip()
                    if source not in {"telegram", "matrix", "whatsapp", "vk", "phone", "sms", "max", "email", "gmail", "yandex"} and not source.startswith("gmail:"):
                        raise ValueError("unsupported conversation source")
                    if not sender:
                        raise ValueError("sender is required")
                    if source=='yandex':
                        from .yandex.web import YandexWeb
                        record=YandexWeb(service,user_id).create_conversation(str(payload.get('account_ref') or ''),sender)
                    elif source=='vk':
                        from .vk_web import VKWeb
                        record=VKWeb(service,user_id).create_conversation(str(payload.get('account_ref') or ''),sender)
                    else:
                        record = service._store.create_conversation(source, sender, user_id=user_id)
                    self._reply(201, {"conversation": record})
                    return
                if path == '/v1/meetings/telemost' or (path.startswith('/v1/meetings/telemost/') and path.endswith('/stop')):
                    if not service._store.capability_enabled('send',user_id=user_id):
                        self._reply(403,{'error':'send_capability_disabled'});return
                    from .telemost.meetings import meetings_for
                    meetings=meetings_for(service)
                    if path.endswith('/stop'):record=meetings.stop(path.split('/')[-2],user_id)
                    else:record=meetings.start(user_id=user_id,**self._json())
                    self._reply(200,{'ok':True,'meeting':record});return
                if path == '/v1/calls/matrix':
                    from .matrix_calls import calls_for
                    payload = self._json()
                    record = calls_for(service).start(user_id=user_id, **payload)
                    self._reply(202, {'ok':True, 'call':record})
                    return
                if path == '/v1/calls/telegram':
                    from .telegram_calls import calls_for
                    record = calls_for(service).start(user_id=user_id, **self._json())
                    self._reply(202, {'ok':True, 'call':record})
                    return
                if path.startswith('/v1/calls/telegram/') and path.endswith('/stop'):
                    if not service._store.capability_enabled('send', user_id=user_id):
                        self._reply(403, {'error':'send_capability_disabled'})
                        return
                    from .telegram_calls import calls_for
                    record = calls_for(service).stop(path.split('/')[-2],user_id)
                    self._reply(200, {'ok':True,'call':record})
                    return
                if path in {'/v1/calls/matrix/noticeplace', '/v1/calls/telegram/noticeplace'}:
                    from .noticeplace_calls import noticeplace_call
                    if not service._store.capability_enabled('send', user_id=user_id):
                        self._reply(403, {'error':'send_capability_disabled'})
                        return
                    self._reply(200, noticeplace_call(service,user_id,self._json(),channel=path.split('/')[3]))
                    return
                if path.startswith('/v1/calls/matrix/') and path.endswith('/stop'):
                    if not service._store.capability_enabled('send', user_id=user_id):
                        self._reply(403, {'error':'send_capability_disabled'})
                        return
                    from .matrix_calls import calls_for
                    record = calls_for(service).stop(path.split('/')[-2],user_id)
                    self._reply(200, {'ok':True,'call':record})
                    return
                if path == "/v1/conversations/set-account":
                    payload = self._json()
                    conversation_id = str(payload.get("conversation_id") or "").strip()
                    account_ref = str(payload.get("account_id") or "").strip()
                    if not conversation_id:
                        raise ValueError("conversation_id is required")
                    updated = service._store.set_conversation_account(
                        conversation_id, account_ref, user_id=user_id
                    )
                    self._reply(200 if updated else 404, {"updated": updated})
                    return
                if path == "/v1/react":
                    if not service._store.capability_enabled("send", user_id=user_id):
                        self._reply(403, {"error": "send_capability_disabled"})
                        return
                    payload = self._json()
                    result = service.react_to_message(
                        conversation_id=str(payload.get("conversation_id") or "").strip(),
                        message_id=str(payload.get("message_id") or "").strip(),
                        emoji=str(payload.get("emoji") or "").strip(),
                        confirm=payload.get("confirm") is True, user_id=user_id,
                    )
                    self._reply(200, result)
                    return
                if path == "/v1/ai-settings":
                    payload = self._json()
                    settings = service._store.ai_settings(user_id=user_id)
                    self._reply(200, {
                        "endpoint": settings["endpoint"] if settings else "",
                        "model": settings["model"] if settings else "",
                        "has_key": settings is not None,
                    })
                    return
                if path == "/v1/preferences/context":
                    payload = self._json()
                    settings = service._store.set_context_settings(
                        message_count=payload.get("message_count"),
                        max_message_count=payload.get("max_message_count"),
                        token_budget=payload.get("token_budget"),
                        max_token_budget=payload.get("max_token_budget"),
                        summary_token_budget=payload.get("summary_token_budget"),
                        summary_retention_days=payload.get("summary_retention_days"),
                        cache_channels=payload.get("cache_channels"),
                        cache_conversation_kinds=payload.get("cache_conversation_kinds"),
                        user_id=user_id,
                    )
                    self._reply(200, {"context": _context_preferences(service, user_id)})
                    return
                if path == "/v1/ai-runs":
                    bridge = os.environ.get("USERIO_BYOK_BRIDGE_URL", "").rstrip("/")
                    bridge_token = os.environ.get("USERIO_BYOK_BRIDGE_TOKEN", "")
                    if not bridge:
                        self._reply(200, {"runs": [], "totals": None})
                        return
                    proxy_request = urllib.request.Request(  # noqa: S310
                        bridge + "/runs?user_id=owner", headers={"Authorization": f"Bearer {bridge_token}"},
                    )
                    try:
                        with urllib.request.urlopen(proxy_request, timeout=10) as bridge_response:
                            self._reply(200, json.loads(bridge_response.read()))
                    except (OSError, urllib.error.URLError):
                        self._reply(200, {"runs": [], "totals": None})
                    return
                if path == "/v1/ai-presets":
                    bridge = os.environ.get("USERIO_BYOK_BRIDGE_URL", "").rstrip("/")
                    bridge_token = os.environ.get("USERIO_BYOK_BRIDGE_TOKEN", "")
                    if not bridge:
                        self._reply(200, {"presets": [], "source": "disabled"})
                        return
                    proxy_request = urllib.request.Request(  # noqa: S310
                        bridge + "/presets", headers={"Authorization": f"Bearer {bridge_token}"},
                    )
                    try:
                        with urllib.request.urlopen(proxy_request, timeout=10) as bridge_response:
                            self._reply(200, json.loads(bridge_response.read()))
                    except (OSError, urllib.error.URLError):
                        self._reply(200, {"presets": [], "source": "unavailable"})
                    return
                if path == "/v1/ai-settings/save":
                    payload = self._json()
                    service._store.set_ai_settings(
                        endpoint=str(payload.get("endpoint") or ""),
                        model=str(payload.get("model") or ""),
                        token=str(payload.get("token") or ""),
                        user_id=user_id,
                    )
                    service._user_generators.pop(user_id, None)
                    self._reply(200, {"ok": True})
                    return
                if path == "/v1/ai-settings/reset":
                    cleared = service._store.clear_ai_settings(user_id=user_id)
                    service._user_generators.pop(user_id, None)
                    self._reply(200, {"cleared": cleared})
                    return
                if path.startswith("/v1/source-cursors/"):
                    source = unquote(path.removeprefix("/v1/source-cursors/")).strip()
                    if not source:
                        raise ValueError("source is required")
                    payload = self._json()
                    cursor = str(payload.get("cursor") or "").strip()
                    if not cursor:
                        raise ValueError("cursor is required")
                    service._store.set_user_preference(f"source_cursor:{source}", cursor, user_id=user_id)
                    self._reply(200, {"source": source, "cursor": cursor})
                    return
                if path == "/v1/drafts/browser-notified":
                    payload = self._json()
                    draft_ids = payload.get("draft_ids") or []
                    if not isinstance(draft_ids, list):
                        raise ValueError("draft_ids must be an array")
                    changed = service._store.mark_drafts_browser_notified(
                        [str(item) for item in draft_ids], user_id=user_id
                    )
                    self._reply(200, {"ok": True, "marked": changed})
                    return
                if path == '/v1/preferences/reading':
                    payload = self._json()
                    enabled = payload.get('auto_read')
                    if not isinstance(enabled,bool): raise ValueError('auto_read must be a boolean')
                    service._store.set_user_preference('auto_read','true' if enabled else 'false',user_id=user_id)
                    self._reply(200,{'auto_read':enabled})
                    return
                if path.startswith('/v1/conversations/') and path.endswith('/read'):
                    payload = self._json()
                    automatic = payload.get('automatic',True)
                    if not isinstance(automatic,bool): raise ValueError('automatic must be a boolean')
                    cid = unquote(path.removeprefix('/v1/conversations/').removesuffix('/read').strip('/'))
                    self._reply(200,service.read_conversation(conversation_id=cid,message_id=str(payload.get('message_id') or ''),automatic=automatic,user_id=user_id))
                    return
                if path == "/v1/preferences/capabilities":
                    payload = self._json()
                    updates = payload.get("capabilities", payload)
                    if not isinstance(updates, dict):
                        raise ValueError("capabilities must be an object")
                    for name, enabled in updates.items():
                        if name not in service._store.USER_CAPABILITIES:
                            raise ValueError(f"unknown user capability: {name}")
                        service._store.set_user_capability(name, bool(enabled), user_id=user_id)
                    self._reply(200, {"capabilities": service._store.user_capabilities(user_id=user_id)})
                    return
                if path == "/v1/preferences/send":
                    payload = self._json()
                    enabled = bool(payload.get("enabled"))
                    service._store.set_user_capability("send", enabled, user_id=user_id)
                    self._reply(200, {"send_enabled": enabled})
                    return
                if path == "/v1/telegram/unread-snapshot":
                    # Only authenticated Telegram ingress, never a dashboard read.
                    if not principal.service_account and not (token and hmac.compare_digest(
                            self.headers.get("Authorization", ""), f"Bearer {token}")):
                        self._reply(403, {"error": "registered ingress required"})
                        return
                    payload = self._json()
                    account_id = str(payload.get("account_id") or "").strip()
                    if not account_id:
                        raise ValueError("account_id required")
                    target_user = service._store.ingress_user(
                        source="telegram", account_id=account_id)
                    if principal.service_account and target_user != user_id:
                        self._reply(403, {"error": "account belongs to another user"})
                        return
                    result = service._store.telegram_unread_snapshot(
                        account_ref=account_id, dialogs=payload.get("dialogs"),
                        observed_at=payload.get("observed_at"), user_id=target_user,
                    )
                    self._reply(202, result)
                    return
                if path == "/v1/telegram/mirror-state":
                    # Only the registered ingress can attest provider read/deletion.
                    # A dashboard read is never Telegram send/read authority.
                    if not principal.service_account and not (token and hmac.compare_digest(
                            self.headers.get("Authorization", ""), f"Bearer {token}")):
                        self._reply(403, {"error": "registered ingress required"})
                        return
                    payload = self._json()
                    account_id = str(payload.get("account_id") or "").strip()
                    if not account_id:
                        raise ValueError("account_id required")
                    target_user = service._store.ingress_user(source="telegram", account_id=account_id)
                    if principal.service_account and target_user != user_id:
                        self._reply(403, {"error": "account belongs to another user"})
                        return
                    result = service._store.telegram_mirror_state(
                        account_ref=account_id, peer_id=str(payload.get("peer_id") or ""), user_id=target_user,
                        read_inbox_max_id=payload.get("read_inbox_max_id"),
                        read_outbox_max_id=payload.get("read_outbox_max_id"),
                        unread_count=payload.get("unread_count"),
                        unread_observed_at=payload.get("unread_observed_at"),
                        deleted_ids=payload.get("deleted_ids"), history_ids=payload.get("history_ids"),
                        scan_before_id=payload.get("scan_before_id"),
                        scan_oldest_id=payload.get("scan_oldest_id"),
                        scan_has_more=payload.get("scan_has_more"),
                        scan_fetched=payload.get("scan_fetched"),
                    )
                    self._reply(202, result)
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
                        message, route_id=route_id, user_id=target_user_id,
                        account_ref=str(payload.get("account_id") or "").strip(),
                    )
                    account_ref = str(payload.get("account_id") or "").strip()
                    if account_ref:
                        service._store.set_conversation_account(
                            conversation_id, account_ref, user_id=target_user_id
                        )
                    draft = None
                    conversation = service._store.conversation(
                        conversation_id, user_id=target_user_id
                    )
                    if (
                        accepted and message.direction == "incoming" and not message.reconciliation
                        and conversation and conversation["response_mode"] == "auto_send"
                        and service._store.evaluate_workspace_event(
                            conversation_id=conversation_id, source=message.source,
                            message_id=message.message_id, user_id=target_user_id,
                        )["allowed"]
                        and not service.manual_approval_required(conversation)
                    ):
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
                if path.startswith('/v1/conversations/') and path.endswith('/uploads'):
                    if principal.service_account or not service._store.capability_enabled('send',user_id=user_id):
                        self._reply(403,{'error':'send_capability_disabled'});return
                    if int(self.headers.get('Content-Length','0'))>3*1024*1024:
                        self._reply(413,{'error':'Вложение должно быть не больше 2 МБ'});return
                    from .vk_web import VKWeb
                    cid=unquote(path.removeprefix('/v1/conversations/').removesuffix('/uploads').strip('/'))
                    conv=service._store.conversation(cid,user_id=user_id)
                    if conv and conv['source']=='max':
                        from .max_web import MaxWeb
                        self._reply(201,MaxWeb(service,user_id).stage(cid,self._json()));return
                    self._reply(201,VKWeb(service,user_id).stage(cid,self._json()));return
                if path.startswith("/v1/conversations/") and path.endswith("/send"):
                    if principal.service_account:
                        self._reply(403, {"error": "Используйте явное подтверждение черновика для автоматической отправки"})
                        return
                    if not service._store.capability_enabled("send", user_id=user_id):
                        self._reply(403, {"error": "send_capability_disabled"})
                        return
                    conversation_id = unquote(path.removeprefix("/v1/conversations/").removesuffix("/send").strip("/"))
                    payload = self._json()
                    draft = service.send_manual_message(conversation_id, body=str(payload.get("body") or ""),
                        request_id=str(payload.get("request_id") or ""), user_id=user_id,
                        upload_id=str(payload.get('upload_id') or ''))
                    self._reply(200, {"id": draft.id, "status": draft.status, "receipt": draft.receipt})
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
                if path.startswith('/v1/max/connections/'):
                    if principal.service_account or principal.read_only or not service._store.capability_enabled('read',user_id=user_id):
                        self._reply(403,{'error':'Войдите в свой UserIO для подключения MAX'});return
                    try:
                        if path=='/v1/max/connections/begin':result=max_accounts.begin(user_id)
                        elif path=='/v1/max/connections/poll':result=max_accounts.poll(user_id,str(self._json().get('login_id') or ''))
                        elif path=='/v1/max/connections/cancel':result=max_accounts.cancel(user_id,str(self._json().get('login_id') or ''))
                        else:self._reply(404,{'error':'unknown MAX connection route'});return
                        self._reply(200,result,{'Cache-Control':'no-store'})
                    except Exception as error:
                        _LOGGER = __import__('logging').getLogger('userio.max_connections')
                        _LOGGER.warning('MAX connection %s failed: %s',path.rsplit('/',1)[-1],type(error).__name__)
                        self._reply(400,{'error':'MAX не подтвердил подключение. Если QR истёк, начните новый вход. Действующие аккаунты не изменены.'})
                    return
                if path == '/v1/phone/sip/connections':
                    if principal.service_account or principal.read_only or not service._store.capability_enabled('read',user_id=user_id):
                        self._reply(403,{'error':'Войдите в свой личный кабинет'});return
                    from .sip_connections import request as sip_request
                    try:
                        payload=self._json()
                        result=sip_request('create',user_id,device_id=payload.get('device_id',''),display_name=payload.get('display_name',''))
                        service._store.register_account(account_id=result['id'],provider='phone',
                            display_name=result['display_name'],can_read=False,can_reply=False,
                            credential_ref='sip-hosted:'+result['sip_username'],user_id=user_id)
                        self._reply(201,result,{'Cache-Control':'no-store'})
                    except (OSError,ValueError):
                        self._reply(400,{'error':'Не удалось выдать SIP-настройки. Проверьте ID телефона и название; повторите позже, если сервер недоступен.'})
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
                if path == '/v1/sms/device-connect':
                    if principal.service_account or not service._store.capability_enabled('read',user_id=user_id):
                        self._reply(403, {'error':'Войдите в свой личный кабинет с правом чтения'})
                        return
                    from .phone_setup import connect_phone
                    result=connect_phone(service,user_id,self._json())
                    result['server_url']=self._base_url().rstrip('/')+'/sms-gateway'
                    self._reply(201,result,{'Cache-Control':'no-store'})
                    return
                if path == "/v1/vk/community/accounts":
                    # Community Long Poll/API tokens may read that community,
                    # not the linked VK ID user's personal inbox.
                    if principal.service_account or not service._store.capability_enabled(
                            "read", user_id=user_id):
                        self._reply(403, {"error": "нужна авторизация пользователя"})
                        return
                    account = connect_vk_community(service, user_id, self._json())
                    self._reply(201, {"accepted": True, **account}, {"Cache-Control": "no-store"})
                    return
                if path == "/v1/vk/personal/accounts":
                    # A VK Messenger token can be imported from any consenting
                    # authorization client. Validate actual personal message
                    # permissions and account identity before saving it.
                    if principal.service_account or not service._store.capability_enabled(
                            "read", user_id=user_id):
                        self._reply(403, {"error": "нужна авторизация пользователя"})
                        return
                    account = connect_vk_personal(service, user_id, self._json())
                    self._reply(201, {"accepted": True, **account}, {"Cache-Control": "no-store"})
                    return
                if path == "/v1/vk/native/accounts":
                    # VK ID applications normally cannot obtain personal
                    # messages permission. Do not accept or store their token
                    # under the pretence that native sync is available.
                    self._reply(409, {
                        "code": "vk_messages_permission_unavailable",
                        "error": "Прямой доступ к личным сообщениям VK через VK ID "
                                 "для этого приложения недоступен",
                    })
                    return
                if path == "/v1/vk/accounts":
                    payload = self._json()
                    external_id = str(payload.get("user_id") or "").strip()
                    display_name = str(payload.get("display_name") or "VK account").strip()
                    if not external_id.isdecimal() or len(external_id) > 20 or int(external_id) <= 0:
                        raise ValueError("VK не подтвердил ID пользователя")
                    # VK ID confirms the identity only. Message access belongs to the
                    # user-owned browser extension and must not be implied here.
                    existing = next(
                        (a for a in service._store.accounts(user_id=user_id)
                         if a["id"] == f"vk:{external_id}"), None)
                    if existing and str(existing.get("credential_ref") or "").startswith("connector:"):
                        # Never downgrade an already authorized direct VK read
                        # session when the user signs in with ordinary VK ID.
                        mode = "vk_personal_messages" if "reply" in existing["capabilities"] else "vk_personal_messages_readonly"
                    else:
                        service._store.register_account(
                            account_id=f"vk:{external_id}", provider="vk", display_name=display_name,
                            can_read=False, can_reply=False, credential_ref=f"vkid:{external_id}", enabled=True,
                            user_id=user_id,
                        )
                        mode = "vkid_identity_only"
                    self._reply(202, {"accepted": True, "account_id": f"vk:{external_id}", "mode": mode})
                    return
                if path in {"/v1/matrix/accounts", "/v1/imap/accounts"}:
                    if principal.service_account or not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "нужна авторизация пользователя с правом чтения"})
                        return
                    payload = self._json()
                    if path == "/v1/matrix/accounts":
                        account = connect_matrix(service, user_id, payload)
                    else:
                        account = connect_imap(service, user_id, payload)
                    self._reply(201, {"accepted": True, **account})
                    return
                if path == "/v1/gmail/accounts":
                    # Backward-compatible browser route, but a new mailbox must
                    # belong to this web principal, NOT the host-wide Himalaya
                    # config shared across other UserIO tenants.
                    if principal.service_account or not service._store.capability_enabled("read", user_id=user_id):
                        self._reply(403, {"error": "нужна авторизация пользователя с правом чтения"})
                        return
                    payload = self._json()
                    email = str(payload.get("email") or "").strip().lower()
                    app_password = re.sub(r"\s+", "", str(payload.get("app_password") or ""))
                    account = connect_imap(
                        service, user_id,
                        {"host": "imap.gmail.com", "port": 993,
                         "username": email, "password": app_password},
                    )
                    self._reply(201, {"accepted": True, "account_id": account["id"],
                                      "display_name": account["display_name"],
                                      "mode": "user_owned_imap"})
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
                if path == "/v1/chatgpt/sessions":
                    payload = self._json()
                    record = chatgpt_sessions.save_session(payload, user=principal.username)
                    slug = record["slug"]
                    service._store.register_account(
                        account_id=f"chatgpt:{slug}", provider="chatgpt",
                        display_name=str(payload.get("email") or payload.get("name") or f"ChatGPT {slug}"),
                        can_read=True, can_reply=True,
                        credential_ref=f"chatgpt-session:{slug}", enabled=True,
                        user_id=user_id,
                    )
                    self._reply(202, {"accepted": True, "account_id": f"chatgpt:{slug}", "slug": slug})
                    return
                if path == "/v1/agent/commands":
                    self._reply(202, agent_channel.enqueue(self._json(), user=principal.username))
                    return
                if path == "/v1/agent/results":
                    agent_channel.push_result(self._json(), user=principal.username)
                    self._reply(202, {"accepted": True})
                    return
                if path.startswith("/v1/vault/sessions/"):
                    name = unquote(path.removeprefix("/v1/vault/sessions/")).strip("/")
                    try:
                        vault.save_session(name, self._json(), user=principal.username)
                    except ValueError as error:
                        self._reply(400, {"error": str(error)})
                        return
                    self._reply(202, {"accepted": True, "name": name})
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
            except PermissionError as error:
                self._reply(409, {"error": str(error)})
            except RuntimeError as error:
                self._reply(502, {"error": str(error)})
            except OSError:
                self._reply(502, {"error": "Соединение прервалось. Результат отправки уточняется; не повторяйте сообщение автоматически."})

        def do_PATCH(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            restricted = self._principal()
            if restricted is not None and restricted.meeting_recorder and not meeting_recorder_http_allowed(self.command, urlparse(self.path).path):
                self._reply(403, {'error': 'meeting_recording_scope_only'})
                return
            if restricted is not None and restricted.messenger_accounts is not None:
                self._reply(403, {'error':'messenger_scope_only'})
                return
            if restricted is not None and restricted.read_only and not read_only_http_allowed(self.command, path):
                self._reply(403, {"error": "read_only_token"})
                return
            principal = self._principal(allow_proxy=True)
            if principal is None:
                self._reply(401, {"error": "unauthorized"})
                return
            try:
                if path.startswith("/v1/conversations/") and "/messages/" in path:
                    if principal.service_account or not service._store.capability_enabled("send", user_id=principal.user_id):
                        self._reply(403, {"error": "Изменение сообщения доступно пользователю с правом отправки"})
                        return
                    conversation_id, message_id = path.removeprefix("/v1/conversations/").split("/messages/", 1)
                    payload = self._json()
                    message = service.edit_max_message(unquote(conversation_id), message_id=unquote(message_id),
                        body=str(payload.get("body") or ""), expected_body=str(payload.get("expected_body") or ""),
                        user_id=principal.user_id)
                    self._reply(200, {"message": message})
                    return
                if path.startswith("/v1/drafts/"):
                    draft_id = path.removeprefix("/v1/drafts/").strip("/")
                    payload = self._json()
                    body = str(payload.get("body") or "")
                    if not draft_id or not body.strip():
                        raise ValueError("draft id and body are required")
                    draft = service._store.update_draft(draft_id, body=body, user_id=principal.user_id)
                    self._reply(200, {"id": draft.id, "status": draft.status, "body": draft.body})
                    return
                self._reply(404, {"error": "not found"})
            except KeyError as error:
                self._reply(404, {"error": str(error)})
            except ValueError as error:
                self._reply(400, {"error": str(error)})
            except (RuntimeError, OSError):
                self._reply(502, {"error": "Не удалось подтвердить изменение. Обновите диалог перед повтором."})

        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            requested_path = urlparse(self.path).path
            restricted = self._principal()
            if restricted is not None and restricted.meeting_recorder and not meeting_recorder_http_allowed(self.command, urlparse(self.path).path):
                self._reply(403, {'error': 'meeting_recording_scope_only'})
                return
            if restricted is not None and restricted.messenger_accounts is not None and requested_path!='/mcp':
                self._reply(403, {'error':'messenger_scope_only'})
                return
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
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                ready = {"jsonrpc": "2.0", "method": "userio/ready", "params": {"endpoint": "/mcp", "username": principal.username}}
                self.wfile.write(sse_message(ready)); self.wfile.flush()
                event = subscriptions.wait(principal.user_id, timeout=30.0)
                if event is not None:
                    self.wfile.write(sse_message(event)); self.wfile.flush()
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
            if requested_path == '/telemost':
                self._static('/telemost.html');return
            if requested_path.startswith("/assets/") and self._static(requested_path):
                return
            if requested_path in {"/docs", "/docs/", "/docs/index.html"}:
                if self._static("/docs.html"):
                    return
            if requested_path == "/download":
                self._html(200, _download_page().encode())
                return
            if requested_path == '/connect-phone':
                if self._static('/connect-phone.html'):
                    return
            if requested_path == "/connect-mcp":
                if self._static("/connect-mcp.html"):
                    return
            # Only credential-free ZIPs are distributed. Obsolete signed CRX
            # builds contained a baked operator token and must stay unavailable.
            if requested_path in {"/vk-userio-extension.zip", "/vk-userio-extension-mv3.zip",
                                  "/chatgpt-cdp-setup.zip"} and self._static(requested_path):
                return
            if requested_path == "/vk/callback":
                if self._principal(allow_proxy=True) is None:
                    self._redirect("/login")
                    return
                body = _vk_connect_page(vkid_app_id).encode()
                self._html(200, body)
                return
            if requested_path in {"/accounts", "/accounts/connect/new", "/vk/connect/new", "/matrix/connect/new", "/mail/connect/new",
                                   "/vk/community/connect/new", "/vk/personal/connect/new"}:
                if self._principal(allow_proxy=True) is None:
                    self._redirect("/login")
                    return
                if self._static("/connect-accounts.html"):
                    return
            if requested_path == "/gmail/connect/new":
                if self._principal(allow_proxy=True) is None:
                    self._redirect("/login")
                    return
                if self._static("/connect-accounts.html"):
                    return
            if requested_path in {"/", "/welcome"}:
                if requested_path == '/welcome' or self._principal(allow_proxy=True) is None:
                    if not self._static('/landing.html'):
                        self._html(200,'<!doctype html><html lang="ru"><title>UserIO</title><h1>Все ваши сообщения в одном месте</h1><a href="/login" style="display:inline-block;padding:24px 40px;font-size:28px">Личный кабинет</a><p><a href="/signup">Создать аккаунт</a></p></html>'.encode())
                    return
                if self._static(requested_path):
                    return
            principal = self._principal(allow_proxy=True)
            if principal is None:
                self._reply(401, {"error": "unauthorized"})
                return
            path = urlparse(self.path).path
            if principal.read_only and not read_only_http_allowed(self.command, path):
                self._reply(403, {"error": "read_only_token"})
                return
            user_id = principal.user_id
            if path == '/v1/meetings/telemost' or path.startswith('/v1/meetings/telemost/'):
                if not service._store.capability_enabled('read',user_id=user_id):
                    self._reply(403,{'error':'read_capability_disabled'});return
                from .telemost.meetings import meetings_for
                meetings=meetings_for(service);parts=path.split('/')
                if len(parts)==4:
                    self._reply(200,{'meetings':meetings.list(user_id)});return
                if len(parts)==5:
                    self._reply(200,{'meeting':meetings.get(parts[4],user_id)});return
                if len(parts)==7 and parts[5]=='audio':
                    if not service._store.capability_enabled('download',user_id=user_id):
                        self._reply(403,{'error':'download_capability_disabled'});return
                    audio=meetings.audio(parts[4],user_id,int(parts[6]));size=audio.stat().st_size
                    start,end=0,size-1;requested=self.headers.get('Range','')
                    if requested:
                        match=re.fullmatch(r'bytes=([0-9]+)-([0-9]*)',requested)
                        if not match:
                            self._reply(416,{'error':'invalid_range'});return
                        start=int(match[1]);end=min(size-1,int(match[2]) if match[2] else size-1)
                        if start>end or start>=size:
                            self._reply(416,{'error':'invalid_range'});return
                    self.send_response(206 if requested else 200)
                    self.send_header('Content-Type','audio/wav');self.send_header('Accept-Ranges','bytes')
                    self.send_header('Content-Length',str(end-start+1));self.send_header('Cache-Control','private, max-age=0')
                    if requested:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    with audio.open('rb') as stream:
                        stream.seek(start);remaining=end-start+1
                        while remaining:
                            data=stream.read(min(65536,remaining))
                            if not data:break
                            self.wfile.write(data);remaining-=len(data)
                    return
                self._reply(404,{'error':'meeting_route_not_found'});return
            if path.startswith('/v1/calls/matrix/'):
                if not service._store.capability_enabled('read', user_id=user_id):
                    self._reply(403, {'error':'read_capability_disabled'})
                    return
                from .matrix_calls import calls_for
                self._reply(200, {'ok':True,'call':calls_for(service).get(path.split('/')[-1],user_id)})
                return
            if path.startswith('/v1/calls/telegram/'):
                if not service._store.capability_enabled('read', user_id=user_id):
                    self._reply(403, {'error':'read_capability_disabled'})
                    return
                from .telegram_calls import calls_for
                self._reply(200, {'ok':True,'call':calls_for(service).get(path.split('/')[-1],user_id)})
                return
            audio_route = re.fullmatch(r'/v1/conversations/([^/]+)/call-audio/([^/]+)/(caller|assistant)', path)
            if audio_route:
                if not service._store.capability_enabled('download', user_id=user_id):
                    self._reply(403, {'error': 'download_capability_disabled'})
                    return
                conversation_id, message_id, track = map(unquote, audio_route.groups())
                conv = service._store.conversation(conversation_id, user_id=user_id)
                message = service._store.message(message_id, source=conv['source'], user_id=user_id) if conv else None
                if not conv or not message or not call_audio.recording(conv, message):
                    self._reply(404, {'error': 'call recording not found'})
                    return
                audio_path = call_audio.archive_for(conv) / message['sender'] / (track + '.wav')
                with audio_path.open('rb') as audio:
                    size = os.fstat(audio.fileno()).st_size
                    try:
                        start, end, status = call_audio.byte_range(self.headers.get('Range'), size)
                    except ValueError:
                        self._reply(416, {'error': 'unsatisfiable range'}, {'Content-Range': f'bytes */{size}'})
                        return
                    self.send_response(status)
                    self.send_header('Content-Type', 'audio/wav')
                    self.send_header('Content-Length', str(end - start + 1))
                    self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'private, no-store')
                    self.send_header('Content-Disposition', f'inline; filename="{track}.wav"')
                    if status == 206:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                    self.end_headers()
                    audio.seek(start)
                    remaining = end - start + 1
                    try:
                        while remaining:
                            chunk = audio.read(min(65536, remaining))
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                return
            if path == "/v1/personal-tokens":
                if principal.service_account:
                    self._reply(403, {"error": "personal login required"})
                    return
                self._reply(200, {"tokens": personal_tokens.list(user_id)},
                            headers={"Cache-Control": "no-store"})
                return
            if path == '/v1/sms/agent-binding':
                if not token or not hmac.compare_digest(self.headers.get('Authorization',''),f'Bearer {token}'):
                    self._reply(403,{'error':'registered gateway required'})
                    return
                from .phone_setup import phone_binding
                self._reply(200,phone_binding(service,query.get('user_id',[''])[0],query.get('account_id',[''])[0]))
                return
            if path == "/v1/runtime":
                self._reply(200, public_runtime_identity)
                return
            if path == "/v1/workspace/secretary/deep-jobs":
                if not principal.service_account:
                    self._reply(403, {"error": "service account required"})
                    return
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"})
                    return
                if any(key not in {"status", "limit"} or len(values) != 1 for key, values in query.items()):
                    self._reply(400, {"error": "invalid secretary job query"})
                    return
                try:
                    jobs = service._store.conversation_secretary_jobs(
                        status=query.get("status", ["accepted"])[0],
                        limit=int(query.get("limit", ["20"])[0]), user_id=user_id,
                    )
                except ValueError as error:
                    self._reply(400, {"error": str(error)})
                    return
                self._reply(200, {"jobs": jobs})
                return
            if path.startswith("/v1/workspace/secretary/deep-jobs/"):
                if not principal.service_account:
                    self._reply(403, {"error": "service account required"})
                    return
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"})
                    return
                if query:
                    self._reply(400, {"error": "invalid secretary job query"})
                    return
                job_id = unquote(path.removeprefix("/v1/workspace/secretary/deep-jobs/"))
                try:
                    job = service._store.conversation_secretary_job(job_id, user_id=user_id)
                except ValueError as error:
                    self._reply(400, {"error": str(error)})
                    return
                except KeyError:
                    self._reply(404, {"error": "secretary job not found"})
                    return
                self._reply(200, {"job": job})
                return
            if path == "/v1/workspace/events":
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"})
                    return
                if any(key not in {"after", "limit"} or len(values) != 1 for key, values in query.items()):
                    self._reply(400, {"error": "invalid workspace event query"})
                    return
                try:
                    after = int(query.get("after", ["0"])[0])
                    limit = int(query.get("limit", ["50"])[0])
                    page = service._store.workspace_events(
                        after=after, limit=limit, user_id=user_id,
                    )
                except ValueError:
                    self._reply(400, {"error": "invalid workspace event cursor or limit"})
                    return
                self._reply(200, {"schema": "universal.workspace-events.v1", **page})
                return
            if path.startswith("/v1/workspace/claims/"):
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"})
                    return
                suffix = path.removeprefix("/v1/workspace/claims/").strip("/")
                if not suffix or "/" in suffix:
                    self._reply(404, {"error": "not found"})
                    return
                try:
                    result = service._store.workspace_claim_log(
                        event_seq=int(suffix), user_id=user_id
                    )
                except (KeyError, ValueError) as error:
                    self._reply(404 if isinstance(error, KeyError) else 400, {"error": str(error)})
                    return
                self._reply(200, result)
                return
            if path == "/v1/inbox":
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"}); return
                source = query.get("channel", [None])[0]
                self._reply(200, {"messages": service._store.new_messages(source=source, user_id=user_id)})
                return
            if path.startswith("/v1/source-cursors/"):
                source = unquote(path.removeprefix("/v1/source-cursors/")).strip()
                if not source:
                    self._reply(400, {"error": "source is required"})
                    return
                cursor = service._store.user_preference(f"source_cursor:{source}", user_id=user_id)
                self._reply(200, {
                    "source": source,
                    "cursor": cursor,
                    "recent_message_ids": service._store.recent_message_ids(
                        source=source, user_id=user_id,
                    ),
                })
                return
            if path == '/v1/preferences/reading':
                self._reply(200,{'auto_read':service._store.user_preference('auto_read',user_id=user_id,default='true')=='true'})
                return
            if path == '/v1/diagnostic/chat':
                from .diagnostic_chats import dispatch
                try:
                    self._reply(200, dispatch(service._store, principal, 'userio.diagnostic.get',
                        {'conversation_id': query.get('conversation_id', [''])[0]}))
                except (PermissionError, KeyError, ValueError) as error:
                    self._reply(403 if isinstance(error, PermissionError) else 404, {'error': str(error)})
                return
            if path == "/v1/preferences/capabilities":
                self._reply(200, {"capabilities": service._store.user_capabilities(user_id=user_id)})
                return
            if path == "/v1/preferences/send":
                self._reply(200, {"send_enabled": service._store.send_enabled(user_id=user_id)})
                return
            if path == "/v1/preferences/context":
                self._reply(200, {"context": _context_preferences(service, user_id)})
                return
            if path in {'/v1/phone/sip/connections','/v1/phone/sip/settings'}:
                if principal.service_account or principal.read_only or not service._store.capability_enabled('read',user_id=user_id):
                    self._reply(403,{'error':'Войдите в свой личный кабинет'});return
                from .sip_connections import request as sip_request
                try:
                    result=sip_request('settings',user_id,account_id=query.get('account_id',[''])[0]) if path.endswith('/settings') else sip_request('list',user_id)
                    self._reply(200,result,{'Cache-Control':'no-store'})
                except (OSError,ValueError):self._reply(400,{'error':'SIP-настройки недоступны. Проверьте подключение и повторите позже.'})
                return
            if path == '/v1/connection-principal':
                self._reply(200,{'user_id':user_id,'username':principal.username,
                    'can_manage':not principal.read_only and not principal.service_account
                                 and service._store.capability_enabled('read',user_id=user_id)},
                    {'Cache-Control':'no-store'})
                return
            if path == "/v1/accounts":
                accounts = service._store.accounts(user_id=user_id)
                caps = service._store.user_capabilities(user_id=user_id)
                for account in accounts:
                    credential_ref = str(account.pop("credential_ref", ""))
                    if account.get('provider')=='sms':
                        account['transport']='native' if credential_ref=='sms-native:default' else 'apk' if credential_ref.startswith('sms-agent:') else 'unconfigured'
                    source = _public_account_source(account, credential_ref)
                    account["source"] = source
                    account["last_message_at"] = service._store.latest_message_at(
                        source, user_id=user_id, account_ref=str(account['id']) if account['provider'] in {'telegram','sms','phone'} else '',
                    )
                    visible = list(account.get("capabilities", []))
                    if not caps["read"]:
                        visible = [cap for cap in visible if cap != "read"]
                    if principal.read_only or not caps["send"]:
                        visible = [cap for cap in visible if cap != "reply"]
                    if not caps["download"]:
                        visible = [cap for cap in visible if cap not in {"download", "media"}]
                    account["capabilities"] = visible
                self._reply(200, {"accounts": accounts})
                return
            if path == "/v1/search":
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"}); return
                text = query.get("q", [""])[0].strip()
                source = query.get("source", [""])[0].strip().lower() or None
                account_ref = query.get("account_ref", [""])[0].strip() or None
                if account_ref and len(account_ref) > 128:
                    self._reply(400, {"error": "invalid account_ref"}); return
                try:
                    limit = max(1, min(int(query.get("limit", ["100"])[0]), 500))
                except ValueError:
                    limit = 100
                results = search_conversations(
                    service._store, text, source=source, account_ref=account_ref,
                    limit=limit, user_id=user_id,
                )
                for entry in results:
                    entry["analysis_policy"] = service._store.evaluate_workspace_chat(
                        conversation_id=str(entry["id"]), user_id=user_id,
                    )
                self._reply(200, {"results": results})
                return
            if path == "/v1/drafts/pending":
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"}); return
                try:
                    limit = max(1, min(int(query.get("limit", ["100"])[0]), 200))
                except ValueError:
                    limit = 100
                self._reply(200, {"drafts": service._store.pending_drafts(limit=limit, user_id=user_id)})
                return
            if path == "/v1/conversations":
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"}); return
                source = query.get("source", [""])[0].strip().lower() or None
                exact_source = query.get("exact_source", [""])[0].strip().lower() in {"1", "true", "yes"}
                account_ref = query.get("account_ref", [""])[0].strip() or None
                if account_ref and len(account_ref) > 128:
                    self._reply(400, {"error": "invalid account_ref"}); return
                conversations = service._store.conversations(
                    source=source, exact_source=exact_source, account_ref=account_ref, user_id=user_id,
                )
                # If the latest message in a conversation is an attachment
                # placeholder ([image], [document] …) replace it with the most
                # recent text body so the chat list and the search box always
                # point at real content the operator can act on.
                placeholder_ids = [
                    str(entry["id"]) for entry in conversations
                    if str(entry.get("preview") or "").startswith("[")
                    and str(entry.get("preview") or "").endswith("]")
                ]
                text_overrides = service._store.last_text_bodies_for(
                    placeholder_ids, user_id=user_id,
                )
                for entry in conversations:
                    entry["analysis_policy"] = service._store.evaluate_workspace_chat(
                        conversation_id=str(entry["id"]), user_id=user_id,
                    )
                    preview = str(entry.get("preview") or "")
                    if preview.startswith("[") and preview.endswith("]"):
                        replacement = text_overrides.get(str(entry["id"]))
                        if replacement:
                            preview = replacement
                    # Conversation lists need a compact preview. The full message
                    # remains available from /v1/conversations/{id}.
                    entry["preview"] = preview[:4096]
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
            if path == "/v1/agent/poll":
                try:
                    self._reply(200, agent_channel.poll(
                        query.get("agent_id", [""])[0],
                        query.get("wait", ["20"])[0],
                        user=principal.username,
                    ))
                except ValueError as error:
                    self._reply(400, {"error": str(error)})
                return
            if path == "/v1/agent/results":
                try:
                    self._reply(200, agent_channel.read_results(
                        user=principal.username,
                        agent_id=query.get("agent_id", [""])[0].strip(),
                        limit=query.get("limit", ["20"])[0],
                    ))
                except ValueError as error:
                    self._reply(400, {"error": str(error)})
                return
            if path == "/v1/agent/status":
                self._reply(200, agent_channel.status(user=principal.username))
                return
            if path == "/v1/chatgpt/sessions":
                self._reply(200, {"accounts": chatgpt_sessions.list_sessions(user=principal.username)})
                return
            if path == "/v1/vault/sessions":
                self._reply(200, {"sessions": vault.list_sessions(user=principal.username)})
                return
            if path.startswith("/v1/vault/sessions/"):
                name = unquote(path.removeprefix("/v1/vault/sessions/")).strip("/")
                try:
                    record = vault.load_session(name, user=principal.username)
                except KeyError as error:
                    self._reply(404, {"error": str(error.args[0])})
                    return
                self._reply(200, record)
                return
            if path.startswith("/v1/conversations/") and path.endswith("/secretary"):
                if principal.role != "owner":
                    self._reply(403, {"error": "owner required"})
                    return
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"})
                    return
                conversation_id = unquote(
                    path.removeprefix("/v1/conversations/").removesuffix("/secretary").strip("/")
                )
                try:
                    result = service._store.conversation_secretary(
                        conversation_id, user_id=user_id,
                    )
                except KeyError as error:
                    self._reply(404, {"error": str(error)})
                    return
                self._reply(200, result)
                return
            if path.startswith("/v1/conversations/") and path.endswith("/analysis-policy"):
                if not service._store.capability_enabled("read", user_id=user_id):
                    self._reply(403, {"error": "read_capability_disabled"})
                    return
                conversation_id = unquote(path.removeprefix("/v1/conversations/").removesuffix("/analysis-policy").strip("/"))
                try:
                    chat = service._store.evaluate_workspace_chat(conversation_id=conversation_id, user_id=user_id)
                except KeyError:
                    self._reply(404, {"error": "conversation not found"})
                    return
                self._reply(200, {"analysis_policy": chat})
                return
            if path.startswith('/v1/conversations/') and path.endswith('/phone-history'):
                if not service._store.capability_enabled('read', user_id=user_id):
                    self._reply(403, {'error':'read_capability_disabled'})
                    return
                conversation_id = unquote(path.removeprefix('/v1/conversations/').removesuffix('/phone-history').strip('/'))
                try:
                    page = service._store.phone_history_page(conversation_id,
                        before_message_id=query.get('before',[''])[0],limit=int(query.get('limit',['100'])[0]),user_id=user_id)
                except KeyError:
                    self._reply(404, {'error':'conversation not found'})
                    return
                except ValueError as error:
                    self._reply(400, {'error':str(error)})
                    return
                conv = service._store.conversation(conversation_id, user_id=user_id)
                if conv:
                    call_audio.enrich(dict(conv, messages=page.get('messages', [])))
                self._reply(200, page)
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
                # Message ids contain ':' (e.g. "900000101:1322"); callers pass
                # them percent-encoded in the path segment.
                real_id, message_id = unquote(real_id), unquote(message_id)
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
                    # Gmail threads can carry a different source than the message
                    # (self-mails land in the other account's conversation).
                    message = service._store.message(message_id, user_id=user_id)
                if message is None or str(message.get("conversation_id") or "") != real_id:
                    self._reply(404, {"error": "message not found"})
                    return
                if source_hint=='max':
                    from .matrix_max_media import download as max_binary
                    try:file=max_binary(service,user_id,conv,message)
                    except (ValueError,RuntimeError,OSError):
                        self._reply(503,{'error':'MAX media is temporarily unavailable or exceeds 2 MiB'});return
                    self._reply_raw(200,file.data,content_type=file.content_type,filename=file.filename);return
                if source_hint=='vk' and str(conv.get('account_ref') or '').startswith('vk:'):
                    from .vk_web import VKWeb
                    try:file=VKWeb(service,user_id).download(real_id,message_id)
                    except (ValueError,RuntimeError,OSError) as error:
                        self._reply(502,{'error':str(error)});return
                    self._reply_raw(200,file['data'],content_type=file['content_type'],filename=file['filename']);return
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
                real_id, message_id = unquote(real_id), unquote(message_id)
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
                    # Gmail threads can carry a different source than the message
                    # (self-mails land in the other account's conversation).
                    message = service._store.message(message_id, user_id=user_id)
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
                    "kind": (
                        placeholder.group(1).lower() if placeholder
                        else (str(attachments[0].get("kind") or "").lower() if attachments else None)
                    ),
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
                            "transcript": a.get("transcript"),
                            "transcription_status": a.get("transcription_status"),
                            "transcription_model": a.get("transcription_model"),
                        }
                        for a in attachments
                    ],
                }
                if source_hint=='vk' and str(conv.get('account_ref') or '').startswith('vk:') and attachments:
                    from .vk_web import VKWeb
                    VKWeb(service,user_id).conversation(real_id,write=False)
                    a=attachments[0]
                    payload.update(available=True,reason='',filename=a['filename'],content_type=a['content_type'],size=a['size'],
                        download_url=f'/v1/conversations/{quote(real_id,safe="")}/media/{quote(message_id,safe="")}/raw')
                    self._reply(200,payload);return
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
            call_audio.enrich(record)
            self._reply(200 if record else 404, record or {"error": "conversation not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            restricted = self._principal()
            if restricted is not None and restricted.meeting_recorder and not meeting_recorder_http_allowed(self.command, urlparse(self.path).path):
                self._reply(403, {'error': 'meeting_recording_scope_only'})
                return
            if restricted is not None and restricted.messenger_accounts is not None:
                self._reply(403, {'error':'messenger_scope_only'})
                return
            if restricted is not None and restricted.read_only and not read_only_http_allowed(self.command, path):
                self._reply(403, {"error": "read_only_token"})
                return
            principal = self._principal(allow_proxy=True)
            if principal is None:
                self._reply(401, {"error": "unauthorized"})
                return
            if path.startswith("/v1/personal-tokens/"):
                if principal.service_account:
                    self._reply(403, {"error": "personal login required"})
                    return
                token_id = unquote(path.removeprefix("/v1/personal-tokens/"))
                self._reply(200, {"revoked": personal_tokens.revoke(principal.user_id, token_id)})
                return
            if path.startswith("/v1/accounts/"):
                account_id = unquote(path.removeprefix("/v1/accounts/").strip("/"))
                existing = next(
                    (a for a in service._store.accounts(user_id=principal.user_id)
                     if a["id"] == account_id), None,
                )
                if existing and account_id.startswith('phone:sip:'):
                    from .sip_connections import request as sip_request
                    try:sip_request('delete',principal.user_id,account_id=account_id)
                    except (OSError,ValueError):
                        self._reply(503,{'error':'Не удалось отключить SIP-аккаунт. Повторите позже; подключение сохранено.'});return
                deleted = service._store.delete_account(account_id, user_id=principal.user_id)
                if deleted and existing and existing['provider']=='whatsapp' and ':account-' in account_id:
                    if service.whatsapp_outbox is not None:
                        try:service.whatsapp_outbox.disconnect(user_id=principal.user_id,account_ref=account_id)
                        except (OSError,ValueError):pass # Disabled account also refuses ingress after restart.
                if deleted and existing and existing["provider"] in {"matrix", "email", "vk", "max"}:
                    ref = str(existing.get("credential_ref") or "")
                    if ref.startswith("connector:"):
                        if existing['provider']=='max':
                            from .max_accounts import close_owned_client
                            close_owned_client(service,principal.user_id,account_id)
                        from .account_connections import ConnectorSecrets
                        try:
                            ConnectorSecrets().delete(ref, user_id=principal.user_id,
                                                      provider=str(existing["provider"]))
                        except (OSError, ValueError, PermissionError):
                            # Credentials are no longer referenced or polled.
                            # An orphan is safer than accidental deletion.
                            pass
                self._reply(200, {"deleted": deleted})
                return
            if path.startswith("/v1/drafts/"):
                draft_id = path.removeprefix("/v1/drafts/").strip("/")
                try:
                    deleted = service._store.delete_draft(draft_id, user_id=principal.user_id)
                except ValueError as error:
                    self._reply(409, {"error": str(error)})
                    return
                self._reply(200, {"deleted": deleted})
                return
            if path.startswith("/v1/vault/sessions/"):
                name = unquote(path.removeprefix("/v1/vault/sessions/")).strip("/")
                try:
                    self._reply(200, vault.delete_session(name, user=principal.username))
                except KeyError as error:
                    self._reply(404, {"error": str(error.args[0])})
                return
            self._reply(404, {"error": "not found"})

        def log_message(self, _format: str, *_args: object) -> None:
            return

    UserIOHandler.max_connections = max_accounts
    return UserIOHandler


def _adapter_for_message(service, message: dict[str, object], user_id: str):
    """Pick the StoredChannelAdapter that owns this message's source."""
    from .adapters import (
        MailChannelAdapter,
        TelegramChannelAdapter,
        WhatsAppChannelAdapter,
        VKChannelAdapter,
        AndroidSmsChannelAdapter,
        MaxChannelAdapter,
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
        "max": MaxChannelAdapter,
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
  <p class="lead">Скачайте коннектор, распакуйте и загрузите как unpacked-расширение в Chrome / Chromium / Brave. Для автоматизации используйте Agent Browser с тем же профилем Chrome, где уже выполнен вход.</p>

  <div class="card">
    <h2>Android — SMS и звонки</h2>
    <p>SMS Agent APK для Android 8+ работает с root и без него. Привяжите телефон к своему личному кабинету и получите отдельный токен устройства. Для голосовых звонков есть отдельная инструкция GSM2SIP.</p>
    <a class="btn" href="/connect-phone">Подключить свой телефон</a>
    <a class="btn secondary" href="/sms-gateway/agent/download" download>Скачать SMS Agent APK</a>
    <a class="btn secondary" href="/connect-phone#calls">Звонки: GSM2SIP и SIP</a>
  </div>

  <div class="card">
    <h2>Universal UserIO Agent <span class="tag">браузерное расширение</span></h2>
    <p>Захват VK Web (чаты, поиск, отправка) + универсальный агент сбора данных с сайтов через пользовательские сессии. Никаких куки и токенов сайтов наружу — только ваш настроенный UserIO.</p>
    <a class="btn" href="/vk-userio-extension-mv3.zip">Скачать для Chrome / Chromium (MV3)</a>
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
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Подключить ВКонтакте — UserIO</title>
<style>body{{font:16px system-ui;max-width:680px;margin:7vh auto;padding:24px;background:#111;color:#eee}}
#status{{margin:18px 0;color:#aaa}}a{{color:#8ab4f8}}h2{{font-size:20px}}
.card{{border:1px solid #343434;border-radius:14px;padding:18px;margin-top:25px;background:#1a1a1a}}
.btn{{display:inline-block;background:#0878f6;color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;font-weight:700}}
li{{margin:8px 0}}code{{background:#252525;padding:2px 5px;border-radius:4px}}
small{{color:#aaa;line-height:1.45}}</style></head>
<body><p><a href="/accounts/connect/new">← Аккаунты UserIO</a></p>
<h1>Подключить ВКонтакте</h1>
<p><b>VK ID</b> подтверждает профиль, но не предоставляет UserIO доступ
к личным сообщениям. VK ограничивает этот API для сторонних приложений.</p>
<section class="card">
  <h2>VK без расширения</h2>
  <p>Для личных переписок есть отдельное подключение VK Мессенджера.
  Оно проверяет доступ к сообщениям перед сохранением аккаунта, затем
  читает историю и новые сообщения с сервера UserIO.</p>
  <p><a class="btn" href="/vk/personal/connect/new">Подключить личные чаты VK</a></p>
</section>
<div id="vk_auth"></div><p id="status">Ожидание VK ID…</p>
<section class="card">
  <h2>Для чтения чатов установите расширение</h2>
  <p>Расширение читает открытую сессию VK в Chrome и может передавать в UserIO
  переписки, видимые в этом профиле. Для работы Chrome должен оставаться запущенным.</p>
  <a class="btn" href="/vk-userio-extension-mv3.zip" download>Скачать расширение для Chrome (ZIP)</a>
  <ol>
    <li>Распакуйте ZIP в постоянную папку.</li>
    <li>Откройте <code>chrome://extensions</code> в Chrome, включите режим разработчика и нажмите «Загрузить распакованное».</li>
    <li>В настройках расширения выберите сервер <code>https://msg.bezrabotnyi.com</code>
        и <b>личный</b> UserIO API-токен вашего пользователя. Общий серверный ключ использовать нельзя.</li>
    <li>Откройте <a href="https://vk.com/im" target="_blank" rel="noreferrer">VK Мессенджер</a> в том же профиле Chrome.</li>
  </ol>
  <small>Важно: это универсальное расширение UserIO с широкими разрешениями на сайты.
    Оно не устанавливается через магазин Chrome и не получает доступ к сообщениям через VK ID.
    <a href="/download">Подробности об установке и разрешениях</a>.</small>
</section>
<script src="https://unpkg.com/@vkid/sdk@<3.0.0/dist-sdk/umd/index.js"></script>
<script>
(() => {{
  const status = document.getElementById('status');
  const VKID = window.VKIDSDK;
  if (!VKID) {{ status.textContent = 'VK ID SDK failed to load'; return; }}
  VKID.Config.init({{app:{safe_app_id}, redirectUrl:location.origin + '/vk/callback',
    responseMode:VKID.ConfigResponseMode.Callback, source:VKID.ConfigSource.LOWCODE,
    scope:''}});
  const tap = new VKID.OneTap();
  tap.render({{container:document.getElementById('vk_auth'), showAlternativeLogin:true}})
    .on(VKID.WidgetEvents.ERROR, error => {{ status.textContent = 'VK ID error'; console.error(error); }})
    .on(VKID.OneTapInternalEvents.LOGIN_SUCCESS, payload => {{
      status.textContent = 'Подтверждаю аккаунт VK…';
      VKID.Auth.exchangeCode(payload.code, payload.device_id)
        .then(async result => {{
          const info = await VKID.Auth.userInfo(result.access_token);
          const profile = info.user || {{}};
          const actualId = String(profile.user_id || result.user_id || '');
          if (!/^[1-9][0-9]*$/.test(actualId)) throw new Error('VK не подтвердил ID пользователя');
          const details = {{user_id:actualId,
            display_name:[profile.first_name, profile.last_name].filter(Boolean).join(' ') || 'VK account'}};
          const response = await fetch('/v1/vk/accounts', {{
            method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(details)
          }});
          const data = await response.json();
          if (!response.ok) throw new Error(data.error || 'Ошибка VK ID');
          return data;
        }})
        .then(() => {{
          status.textContent = 'VK ID подтверждён. Чтение личных сообщений '
            + 'через сервер пока недоступно.';
        }})
        .catch(error => {{ status.textContent = 'Ошибка подключения VK ID: ' + error.message; }});
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
    subprocess.run(["systemctl", "restart", "userio-gmail-ingress.service"], check=True, timeout=30)
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
