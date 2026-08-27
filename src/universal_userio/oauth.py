"""Small RFC 8414/7591 OAuth authorization-code provider for the MCP endpoint."""

from __future__ import annotations

import base64
import hashlib
import html
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .contracts import UserPrincipal
from .store import SQLiteUserIOStore


_AUTH_METHODS = {"none", "client_secret_basic", "client_secret_post"}


@dataclass(frozen=True)
class OAuthError(Exception):
    error: str
    description: str
    status: int = 400


class OAuthProvider:
    """Protocol logic, deliberately separate from the stdlib HTTP handler."""

    def __init__(self, store: SQLiteUserIOStore) -> None:
        self._store = store

    @staticmethod
    def protected_resource_metadata(base_url: str) -> dict[str, object]:
        return {
            "resource": base_url + "/mcp",
            "authorization_servers": [base_url],
        }

    @staticmethod
    def authorization_server_metadata(base_url: str) -> dict[str, object]:
        return {
            "issuer": base_url,
            "authorization_endpoint": base_url + "/authorize",
            "token_endpoint": base_url + "/token",
            "registration_endpoint": base_url + "/register",
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "response_types_supported": ["code"],
            "code_challenge_methods_supported": ["S256"],
        }

    def register(self, payload: dict[str, object]) -> dict[str, object]:
        redirect_uris = payload.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris:
            raise OAuthError("invalid_client_metadata", "redirect_uris is required")
        if not all(isinstance(uri, str) and self._valid_redirect_uri(uri) for uri in redirect_uris):
            raise OAuthError("invalid_redirect_uri", "redirect_uris must be absolute HTTP(S) URLs")
        if len(set(redirect_uris)) != len(redirect_uris):
            raise OAuthError("invalid_client_metadata", "redirect_uris must be unique")
        method = str(payload.get("token_endpoint_auth_method") or "client_secret_basic")
        if method not in _AUTH_METHODS:
            raise OAuthError("invalid_client_metadata", "unsupported token_endpoint_auth_method")
        client_id, client_secret = self._store.register_oauth_client(
            redirect_uris=redirect_uris, token_endpoint_auth_method=method
        )
        result: dict[str, object] = {
            "client_id": client_id,
            "client_id_issued_at": int(time.time()),
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": method,
        }
        if client_secret is not None:
            result["client_secret"] = client_secret
        return result

    def authorize_request(self, values: dict[str, str]) -> dict[str, object]:
        if values.get("response_type") != "code":
            raise OAuthError("unsupported_response_type", "response_type must be code")
        client_id, redirect_uri = values.get("client_id", ""), values.get("redirect_uri", "")
        client = self._store.oauth_client(client_id)
        if client is None:
            raise OAuthError("unauthorized_client", "unknown client_id")
        if redirect_uri not in client["redirect_uris"]:
            raise OAuthError("invalid_request", "redirect_uri is not registered")
        scope = values.get("scope", "").strip()
        if scope and scope != "userio":
            raise OAuthError("invalid_scope", "only the userio scope is available")
        challenge, method = values.get("code_challenge", ""), values.get("code_challenge_method", "")
        public = client["token_endpoint_auth_method"] == "none"
        if public and (not challenge or method != "S256"):
            raise OAuthError("invalid_request", "public clients require S256 PKCE")
        if challenge and method != "S256":
            raise OAuthError("invalid_request", "code_challenge_method must be S256")
        if method and not challenge:
            raise OAuthError("invalid_request", "code_challenge is required with its method")
        return {
            "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "scope": scope or "userio",
            "state": values.get("state", ""), "code_challenge": challenge or None,
            "code_challenge_method": method,
            "username": values.get("username", ""),
        }

    def authorization_form(self, request: dict[str, object], principal: UserPrincipal | None) -> bytes:
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(key)}" value="{html.escape(str(value or ""))}">'
            for key, value in request.items()
            if key in {
                "response_type", "client_id", "redirect_uri", "scope", "state",
                "code_challenge", "code_challenge_method",
            }
        )
        who = (
            f"<p>Вы вошли как <strong>{html.escape(principal.username)}</strong>.</p>"
            if principal else
            '<label>Логин <input name="username" autocomplete="username" required></label>'
            '<label>Пароль <input type="password" name="password" autocomplete="current-password" required></label>'
        )
        return f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Разрешить доступ UserIO</title>
<style>body{{font:16px system-ui;max-width:420px;margin:8vh auto;padding:20px}}label,input,button{{display:block;width:100%;box-sizing:border-box;margin:10px 0;padding:9px}}</style>
<h1>Подключение UserIO</h1><p>Приложение получит полный доступ к вашим каналам UserIO (<code>userio</code>).</p>
<form method="post" action="/authorize">{hidden}{who}<button name="consent" value="allow">Войти и разрешить</button></form>
</html>""".encode()

    def authorize(self, values: dict[str, str], session_user: UserPrincipal | None) -> tuple[str, UserPrincipal | None]:
        request = self.authorize_request(values)
        if values.get("consent") != "allow":
            raise OAuthError("access_denied", "consent is required")
        principal = session_user
        if principal is None:
            principal = self._store.authenticate_credentials(
                values.get("username", ""), values.get("password", "")
            )
        if principal is None:
            raise OAuthError("access_denied", "invalid username or password", 401)
        code = self._store.create_oauth_code(
            user_id=principal.user_id, client_id=str(request["client_id"]),
            redirect_uri=str(request["redirect_uri"]),
            code_challenge=request["code_challenge"] if isinstance(request["code_challenge"], str) else None,
            scope=str(request["scope"]),
        )
        redirect = self._redirect(
            str(request["redirect_uri"]), code=code, state=str(request["state"])
        )
        return redirect, principal

    def token(self, values: dict[str, str], basic_client: tuple[str, str] | None) -> dict[str, object]:
        grant_type = values.get("grant_type", "")
        client_id, secret = self._client_credentials(values, basic_client)
        client = self._store.authenticate_oauth_client(client_id, secret)
        if client is None:
            raise OAuthError("invalid_client", "client authentication failed", 401)
        if grant_type == "authorization_code":
            code, redirect_uri = values.get("code", ""), values.get("redirect_uri", "")
            if not code or redirect_uri not in client["redirect_uris"]:
                raise OAuthError("invalid_grant", "invalid authorization code")
            verifier = values.get("code_verifier", "")
            challenge = None
            if client["token_endpoint_auth_method"] == "none":
                if not verifier:
                    raise OAuthError("invalid_grant", "code_verifier is required")
                challenge = self._pkce_challenge(verifier)
            elif verifier:
                challenge = self._pkce_challenge(verifier)
            redeemed = self._store.redeem_oauth_code(
                code=code, client_id=client_id, redirect_uri=redirect_uri, code_challenge=challenge
            )
            if redeemed is None:
                raise OAuthError("invalid_grant", "authorization code is invalid, expired, or used")
            user_id, scope = redeemed
            return self._store.issue_oauth_tokens(user_id=user_id, client_id=client_id, scope=scope)
        if grant_type == "refresh_token":
            refreshed = self._store.rotate_oauth_refresh(
                refresh_token=values.get("refresh_token", ""), client_id=client_id
            )
            if refreshed is None:
                raise OAuthError("invalid_grant", "refresh token is invalid, expired, or used")
            return refreshed
        raise OAuthError("unsupported_grant_type", "grant_type is unsupported")

    @staticmethod
    def _client_credentials(
        values: dict[str, str], basic_client: tuple[str, str] | None
    ) -> tuple[str, str | None]:
        form_id, form_secret = values.get("client_id", ""), values.get("client_secret")
        if basic_client is not None:
            if form_id and form_id != basic_client[0]:
                raise OAuthError("invalid_client", "conflicting client_id", 401)
            return basic_client
        if not form_id:
            raise OAuthError("invalid_client", "client_id is required", 401)
        return form_id, form_secret

    @staticmethod
    def _pkce_challenge(verifier: str) -> str:
        return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    @staticmethod
    def _valid_redirect_uri(uri: str) -> bool:
        parsed = urlsplit(uri)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc) and not parsed.fragment

    @staticmethod
    def _redirect(uri: str, *, code: str, state: str) -> str:
        parsed = urlsplit(uri)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        query.append(("code", code))
        if state:
            query.append(("state", state))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))
