"""Native DION authentication owned by UserIO.

DION uses a rotating one-time refresh credential. Exactly one UserIO backend
owns a connected account's refresh chain. Browser/UI code is only an optional
bootstrap path; chats and calls use this module directly.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Mapping

DION_API = "https://api.dion.vc"
DION_API_CLIENTS = "https://api-clients.dion.vc"
_MAX_TOKEN = 16 * 1024
_REFRESH_SKEW = 60
_AUTH_LOCK = threading.RLock()
_ID = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")


@contextmanager
def _chain_lock(vault: Any, user_id: str, account_id: str):
    """Serialize the single-use chain across backend processes, not just threads."""
    with _AUTH_LOCK:
        vault.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if vault.root.is_symlink() or vault.root.stat().st_mode & 0o077:
            raise DionAuthError("DION credential directory must be private")
        key = hashlib.sha256((user_id + "\0" + account_id).encode()).hexdigest()
        fd = os.open(vault.root / (".dion-" + key + ".lock"),
                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)


def _sync_vault(vault: Any) -> None:
    fd = os.open(vault.root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class DionAuthError(ValueError):
    pass


class DionGenerationConflict(DionAuthError):
    def __init__(self, current_generation: int):
        super().__init__("stale DION credential generation")
        self.current_generation = current_generation


class DionReauthRequired(DionAuthError):
    pass


@dataclass(frozen=True)
class DionIdentity:
    user_id: str
    organization_id: str
    display_name: str


def _pick(data: Mapping[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        value: Any = data
        for part in path:
            if not isinstance(value, Mapping):
                value = None
                break
            value = value.get(part)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def parse_identity(payload: Mapping[str, Any]) -> DionIdentity:
    user_id = _pick(
        payload,
        ("user", "id"), ("user", "userId"), ("id",), ("userId",), ("user_id",),
    )
    organization_id = _pick(
        payload,
        ("organization", "id"), ("organization", "organizationId"),
        ("organizationId",), ("organization_id",), ("org_id",),
        ("tenantId",), ("tenant_id",),
    )
    display_name = _pick(
        payload,
        ("user", "displayName"), ("user", "name"),
        ("displayName",), ("name",), ("email",),
    ) or user_id
    if not _ID.fullmatch(user_id) or not _ID.fullmatch(organization_id):
        raise DionAuthError("DION did not confirm immutable user and organization identity")
    return DionIdentity(user_id, organization_id, display_name[:160])


def _json_request(
    method: str,
    path: str,
    *,
    base: str = DION_API,
    access_token: str = "",
    refresh_token: str = "",
    body: Mapping[str, Any] | None = None,
    opener: Any = None,
    allow_missing: bool = False,
) -> tuple[dict[str, Any], Mapping[str, str]]:
    if not path.startswith("/"):
        raise ValueError("DION path must be absolute")
    raw = None if body is None else json.dumps(dict(body), separators=(",", ":")).encode()
    headers = {
        "Accept": "application/json",
        "Origin": "https://dion.vc",
        "Referer": "https://dion.vc/",
        "User-Agent": "Universal-UserIO/1",
    }
    if raw is not None:
        headers["Content-Type"] = "application/json"
    if access_token:
        headers["Authorization"] = "Bearer " + access_token
    if refresh_token:
        headers["Cookie"] = "vc-refresh-token=" + refresh_token
    request = urllib.request.Request(base + path, method=method, data=raw, headers=headers)
    run = opener or urllib.request.build_opener(urllib.request.ProxyHandler({})).open
    try:
        with run(request, timeout=12) as response:
            data = response.read(1_000_001)
            if len(data) > 1_000_000:
                raise DionAuthError("DION response is too large")
            result = json.loads(data or b"{}")
            if not isinstance(result, dict):
                raise DionAuthError("DION returned invalid JSON")
            return result, response.headers
    except urllib.error.HTTPError as exc:
        if allow_missing and exc.code in (404, 405):
            raise FileNotFoundError("DION endpoint unavailable") from exc
        if exc.code in (401, 403):
            raise DionReauthRequired("DION authentication is no longer valid") from exc
        raise DionAuthError("DION request failed") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise DionAuthError("DION is temporarily unavailable") from exc


def login_dion_credentials(email: str, password: str, *, opener: Any = None) -> dict[str, str]:
    email = str(email).strip()
    password = str(password)
    if not email or "@" not in email or len(email) > 320 or not password or len(password) > 4096:
        raise DionAuthError("DION email or password is invalid")
    body = {"email": email, "password": password}
    try:
        payload, _ = _json_request(
            "POST", "/v2/users/login/web", base=DION_API_CLIENTS,
            body=body, opener=opener, allow_missing=True,
        )
    except FileNotFoundError:
        payload, _ = _json_request(
            "POST", "/platform/v2/auth/auth-providers/dion/login/password",
            body=body, opener=opener,
        )
    access = _pick(payload, ("access_token",), ("accessToken",))
    refresh = _pick(payload, ("refresh_token",), ("refreshToken",))
    if not access or not refresh or max(len(access), len(refresh)) > _MAX_TOKEN:
        raise DionAuthError("DION did not return a complete credential pair")
    return {
        "access_token": access,
        "refresh_token": refresh,
        "email": email,
    }


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload.encode()))
        return decoded if isinstance(decoded, dict) else {}
    except (ValueError, IndexError, json.JSONDecodeError):
        return {}


def verify_dion_identity(access_token: str, refresh_token: str = "", *, opener: Any = None) -> DionIdentity:
    payload, _ = _json_request(
        "GET", "/platform/v1/whoami", access_token=access_token, opener=opener,
    )
    claims = _jwt_claims(access_token)
    merged = dict(payload)
    if not _pick(merged, ("user", "id"), ("user", "userId"), ("id",), ("userId",), ("user_id",)):
        merged["user_id"] = str(claims.get("user_id") or "")
    if not _pick(merged, ("organization", "id"), ("organization", "organizationId"),
                 ("organizationId",), ("organization_id",), ("org_id",),
                 ("tenantId",), ("tenant_id",)):
        merged["org_id"] = str(claims.get("org_id") or "")
    return parse_identity(merged)


def _tokens_from_headers(headers: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    get_all = getattr(headers, "get_all", None)
    values = list(get_all("Set-Cookie") or []) if callable(get_all) else []
    if not values and headers.get("Set-Cookie"):
        values = [str(headers["Set-Cookie"])]
    for value in values:
        cookie = SimpleCookie()
        cookie.load(value)
        for name, key in (("vc-access-token", "access_token"), ("vc-refresh-token", "refresh_token")):
            if name in cookie and cookie[name].value:
                result[key] = cookie[name].value
    return result


def refresh_dion_tokens(refresh_token: str, *, opener: Any = None) -> dict[str, str]:
    if not refresh_token or len(refresh_token) > _MAX_TOKEN:
        raise DionReauthRequired("DION refresh credential is unavailable")
    payload, headers = _json_request(
        "POST", "/platform/v1/auth/refresh/web",
        refresh_token=refresh_token, opener=opener,
    )
    tokens = _tokens_from_headers(headers)
    tokens["access_token"] = tokens.get("access_token") or _pick(
        payload, ("access_token",), ("accessToken",),
    )
    tokens["refresh_token"] = tokens.get("refresh_token") or _pick(
        payload, ("refresh_token",), ("refreshToken",),
    )
    if not tokens["access_token"] or not tokens["refresh_token"]:
        raise DionReauthRequired("DION did not rotate the credential chain")
    return tokens


def _jwt_expiry(token: str) -> int:
    try:
        return int(_jwt_claims(token).get("exp") or 0)
    except (TypeError, ValueError):
        return 0


def _account(service: Any, user_id: str, account_id: str) -> dict[str, Any] | None:
    return next(
        (a for a in service._store.accounts(user_id=user_id)
         if a["id"] == account_id and a["provider"] == "dion"),
        None,
    )


def _save_chain(
    service: Any,
    user_id: str,
    account_id: str,
    identity: DionIdentity,
    secret: Mapping[str, Any],
    *,
    vault: Any,
    old_ref: str = "",
) -> tuple[str, int]:
    generation = int(secret.get("generation") or 0)
    reference = vault.save({
        "provider": "dion",
        "user_id": user_id,
        "account_id": account_id,
        "provider_user_id": identity.user_id,
        "organization_id": identity.organization_id,
        "display_name": identity.display_name,
        "access_token": str(secret.get("access_token") or ""),
        "refresh_token": str(secret.get("refresh_token") or ""),
        **({"email": str(secret.get("email") or "")} if secret.get("email") else {}),
        "generation": generation,
        "state": str(secret.get("state") or "connected"),
    })
    # Make the secret directory entry durable before publishing its DB reference.
    _sync_vault(vault)
    service._store.register_account(
        account_id=account_id,
        provider="dion",
        display_name=identity.display_name or account_id,
        can_read=True,
        can_reply=True,
        credential_ref=reference,
        enabled=True,
        user_id=user_id,
    )
    service._store.bind_channel_route(user_id=user_id, source="dion", route_id="dion")
    if old_ref.startswith("connector:") and old_ref != reference:
        try:
            vault.delete(old_ref, user_id=user_id, provider="dion")
        except FileNotFoundError:
            pass
    return reference, generation


def connect_dion(
    service: Any,
    user_id: str,
    payload: Mapping[str, Any],
    *,
    vault: Any,
    verifier: Any = verify_dion_identity,
    login: Any = login_dion_credentials,
) -> dict[str, Any]:
    """Connect DION and transfer refresh-chain ownership to UserIO."""
    expected_raw = payload.get("expected_generation")
    access = str(payload.get("access_token") or "")
    refresh = str(payload.get("refresh_token") or "")
    email = str(payload.get("email") or "").strip()
    password = str(payload.get("password") or "")
    password_login = bool(email or password)
    if expected_raw is None:
        if not password_login:
            raise DionAuthError("DION credential handoff requires expected_generation")
        expected = None
    else:
        expected = expected_raw
        if type(expected) is not int or expected < 0:
            raise DionAuthError("invalid DION credential generation")

    if password_login:
        if access or refresh:
            raise DionAuthError("choose DION password login or credential handoff, not both")
        credentials = login(email, password)
        access = credentials["access_token"]
        refresh = credentials["refresh_token"]
    elif not access or not refresh or max(len(access), len(refresh)) > _MAX_TOKEN:
        raise DionAuthError("DION credentials are incomplete")

    identity = verifier(access, refresh)
    expected_user = str(payload.get("provider_user_id") or "").strip()
    expected_org = str(payload.get("organization_id") or "").strip()
    if expected_user and expected_user != identity.user_id:
        raise DionAuthError("DION credential belongs to another user")
    if expected_org and expected_org != identity.organization_id:
        raise DionAuthError("DION credential belongs to another organization")

    account_id = f"dion:{identity.organization_id}:{identity.user_id}"
    with _chain_lock(vault, user_id, account_id):
        existing = _account(service, user_id, account_id)
        old_ref = str(existing.get("credential_ref") or "") if existing else ""
        current = 0
        if old_ref.startswith("connector:"):
            old = vault.load(old_ref, user_id=user_id, provider="dion")
            current = int(old.get("generation") or 0)
        # A deliberate password login is a fresh provider login performed by
        # this authenticated UserIO owner and may replace the current chain.
        # Imported token bundles remain strict compare-and-swap operations.
        if expected is not None and expected != current:
            raise DionGenerationConflict(current)
        generation = current + 1
        _save_chain(
            service, user_id, account_id, identity, {
                "access_token": access,
                "refresh_token": refresh,
                "email": email,
                "generation": generation,
                "state": "connected",
            },
            vault=vault,
            old_ref=old_ref,
        )
    return {
        "id": account_id,
        "source": account_id,
        "display_name": identity.display_name,
        "generation": generation,
        "state": "connected",
        "provider_user_id": identity.user_id,
        "organization_id": identity.organization_id,
    }


class DionAuthBroker:
    """Single refresh owner shared by DION chats and call runtime."""

    def __init__(
        self,
        service: Any,
        user_id: str,
        *,
        vault: Any,
        refresher: Any = refresh_dion_tokens,
        verifier: Any = verify_dion_identity,
        login: Any = login_dion_credentials,
        clock: Any = time.time,
    ):
        self.service = service
        self.user_id = user_id
        self.vault = vault
        self.refresher = refresher
        self.verifier = verifier
        self.login = login
        self.clock = clock

    def load(self, account_id: str) -> dict[str, Any]:
        account = _account(self.service, self.user_id, account_id)
        if account is None or not account["enabled"]:
            raise DionAuthError("DION account is not enabled")
        ref = str(account.get("credential_ref") or "")
        if not ref.startswith("connector:"):
            raise DionAuthError("DION account has no managed credential")
        secret = self.vault.load(ref, user_id=self.user_id, provider="dion")
        return {**secret, "_credential_ref": ref}

    def access_token(self, account_id: str) -> str:
        with _chain_lock(self.vault, self.user_id, account_id):
            secret = self.load(account_id)
            secret = self._validate_pending(account_id, secret)
            token = str(secret.get("access_token") or "")
            expiry = _jwt_expiry(token)
            if token and (not expiry or expiry - int(self.clock()) > _REFRESH_SKEW):
                return token
            return self._rotate_locked(account_id, secret)["access_token"]

    def refresh(self, account_id: str, *, rejected_access_token: str | None = None) -> dict[str, Any]:
        with _chain_lock(self.vault, self.user_id, account_id):
            secret = self._validate_pending(account_id, self.load(account_id))
            if rejected_access_token is not None and secret.get("access_token") != rejected_access_token:
                return secret
            return self._rotate_locked(account_id, secret)

    def _validate_pending(self, account_id: str, secret: Mapping[str, Any]) -> dict[str, Any]:
        state = secret.get("state")
        if state == "reauth_required":
            raise DionReauthRequired("DION requires a fresh credential handoff")
        if state == "refresh_pending":
            raise DionAuthError("DION refresh outcome is unknown; a fresh handoff is required")
        if state != "identity_pending":
            return dict(secret)
        identity = self.verifier(str(secret["access_token"]), str(secret["refresh_token"]))
        if (identity.user_id != secret.get("provider_user_id")
                or identity.organization_id != secret.get("organization_id")):
            raise DionReauthRequired("DION refresh changed account identity")
        validated = {**secret, "state": "connected"}
        ref, _ = _save_chain(self.service, self.user_id, account_id, identity, validated,
                            vault=self.vault, old_ref=str(secret.get("_credential_ref") or ""))
        return {**validated, "_credential_ref": ref}

    def _rotate_locked(self, account_id: str, secret: Mapping[str, Any]) -> dict[str, Any]:
        old_ref = str(secret.get("_credential_ref") or "")
        identity = DionIdentity(str(secret.get("provider_user_id") or ""),
                                str(secret.get("organization_id") or ""),
                                str(secret.get("display_name") or ""))
        # Crash/network ambiguity must never replay a potentially consumed token.
        old_ref, _ = _save_chain(self.service, self.user_id, account_id, identity,
                                 {**secret, "state": "refresh_pending"},
                                 vault=self.vault, old_ref=old_ref)
        try:
            rotated = self.refresher(str(secret.get("refresh_token") or ""))
        except DionReauthRequired:
            identity = DionIdentity(
                str(secret.get("provider_user_id") or ""),
                str(secret.get("organization_id") or ""),
                str(secret.get("display_name") or ""),
            )
            marked = {
                **{k: v for k, v in secret.items() if not str(k).startswith("_")},
                "state": "reauth_required",
            }
            _save_chain(
                self.service, self.user_id, account_id, identity, marked,
                vault=self.vault, old_ref=old_ref,
            )
            raise

        access = str(rotated.get("access_token") or "")
        refresh = str(rotated.get("refresh_token") or "")
        generation = int(secret.get("generation") or 0) + 1
        new_secret = {
            **{k: v for k, v in secret.items() if not str(k).startswith("_")},
            "access_token": access,
            "refresh_token": refresh,
            "generation": generation,
            "state": "identity_pending",
        }
        ref, _ = _save_chain(
            self.service, self.user_id, account_id, identity, new_secret,
            vault=self.vault, old_ref=old_ref,
        )
        return self._validate_pending(account_id, {**new_secret, "_credential_ref": ref})


def dion_account_states(service: Any, user_id: str, *, vault: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for account in service._store.accounts(user_id=user_id):
        if account["provider"] != "dion":
            continue
        generation = 0
        state = "unconfigured"
        ref = str(account.get("credential_ref") or "")
        if ref.startswith("connector:"):
            try:
                secret = vault.load(ref, user_id=user_id, provider="dion")
                generation = int(secret.get("generation") or 0)
                state = str(secret.get("state") or "connected")
            except (OSError, ValueError, PermissionError, json.JSONDecodeError):
                state = "credential_error"
        result.append({
            "id": account["id"],
            "display_name": account["display_name"],
            "generation": generation,
            "state": state,
            "enabled": bool(account["enabled"]),
        })
    return result
