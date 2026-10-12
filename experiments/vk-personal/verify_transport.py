#!/usr/bin/env python3
"""Independent VK Messenger transport proof. NOT a UserIO production connector.

Only VK-owned HTTPS API hosts are contacted. The QR/OAuth redirect fragment
is read interactively and never logged. The earlier token posted in a chat
must not be used: obtain a fresh one before the live test.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

API = "https://api.vk.ru/method/"
CLIENT_ID = "51453752"  # VK Messenger client identifier, not UserIO VK ID
VERSION = "5.263"
READ_METHODS = frozenset({
    "auth.getAnonymToken",
    "auth.exchangeSilentAuthToken",
    "auth.getExchangeToken",
    "users.get",
    "messages.getConversations",
    "messages.getHistory",
    "messages.getLongPollServer",
})
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{10,4096}$")


class VKRejected(Exception):
    def __init__(self, code: object, method: str = ""):
        self.code = code
        self.method = method
        super().__init__(f"VK API denied {method}: code {code}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Refusing to forward VK authorization to a redirect")


def vk_call(method: str, *, opener: Callable[..., Any] | None = None, **fields: object) -> Any:
    if method not in READ_METHODS:
        raise ValueError("This proof is strictly read-only")
    data = urllib.parse.urlencode({"v": VERSION, **fields}).encode()
    request = urllib.request.Request(
        API + method,
        data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    open_request = opener or urllib.request.build_opener(NoRedirect).open
    try:
        with open_request(request, timeout=12) as response:
            body = response.read(2_000_001)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as exc:
        raise RuntimeError("VK API transport unavailable") from exc
    if len(body) > 2_000_000:
        raise RuntimeError("Oversized VK response")
    try:
        parsed = json.loads(body)
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("Invalid VK API response") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("Unexpected VK API response")
    if isinstance(parsed.get("error"), dict):
        raise VKRejected(parsed["error"].get("error_code"), method)
    result = parsed.get("response")
    if result is None:
        raise RuntimeError("VK API omitted response")
    return result


def parse_login_result(value: str) -> dict[str, str]:
    """Accept VK implicit access token or newer #payload=silent_token redirect."""
    value = value.strip()
    if TOKEN_PATTERN.fullmatch(value):
        return {"kind": "access_token", "token": value, "uuid": "", "vk_id": ""}
    url = urllib.parse.urlsplit(value)
    if (url.scheme != "https" or url.hostname not in {"oauth.vk.ru", "oauth.vk.com"}
            or url.path != "/blank.html" or url.port not in (None, 443)
            or url.username or url.password):
        raise ValueError("Expected a VK OAuth blank.html authorization result")
    fragment = urllib.parse.parse_qs(url.fragment, keep_blank_values=True)
    if len(fragment.get("access_token", [])) == 1:
        token = fragment["access_token"][0]
        if not TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Invalid VK access_token value")
        return {"kind": "access_token", "token": token, "uuid": "",
                "vk_id": fragment.get("user_id", [""])[0]}
    if len(fragment.get("payload", [])) != 1:
        raise ValueError("VK did not provide an access_token or silent_token payload")
    try:
        payload = json.loads(fragment["payload"][0])
    except (ValueError, TypeError) as exc:
        raise ValueError("Malformed VK silent token payload") from exc
    if not isinstance(payload, dict) or payload.get("type") != "silent_token":
        raise ValueError("VK OAuth payload is not a silent_token")
    token = str(payload.get("token") or "")
    if not TOKEN_PATTERN.fullmatch(token):
        raise ValueError("Invalid VK silent_token value")
    user = payload.get("user") or {}
    vk_id = user.get("id") if isinstance(user, dict) else None
    if type(vk_id) is not int or vk_id <= 0:
        raise ValueError("Missing VK user identity")
    ttl = payload.get("ttl")
    if type(ttl) is not int or not 1 <= ttl <= 86400:
        raise ValueError("Invalid VK silent token TTL")
    uuid = str(payload.get("uuid") or "")
    if len(uuid) > 256 or "\n" in uuid:
        raise ValueError("Invalid VK silent token UUID")
    return {"kind": "silent_token", "token": token,
            "uuid": uuid, "vk_id": str(vk_id), "ttl": str(ttl)}


def resolve_user_access(
    parsed: dict[str, str], *, call: Callable[..., Any] = vk_call,
    progress: Callable[[str], None] | None = None,
) -> tuple[str, str, int | None]:
    if parsed["kind"] == "access_token":
        if progress:
            progress("VK_USER_TOKEN_PROVIDED")
        return parsed["token"], parsed.get("vk_id", ""), None
    # Fast Messenger also supplies client_secret here. For this app id, our
    # real VK probe succeeded without it. An operator may set it via a private
    # process environment if needed; never hardcode it or echo it to logs.
    anonymous_parameters = {"client_id": CLIENT_ID}
    client_secret = os.environ.get("VK_MESSENGER_CLIENT_SECRET")
    if client_secret:
        anonymous_parameters["client_secret"] = client_secret
    anon = call("auth.getAnonymToken", **anonymous_parameters)
    if not isinstance(anon, dict):
        raise RuntimeError("VK anonymous token response is invalid")
    anon_token = str(anon.get("token") or "")
    if not TOKEN_PATTERN.fullmatch(anon_token):
        raise RuntimeError("VK did not provide an anonymous token")
    if progress:
        progress("VK_ANONYMOUS_TOKEN_OK")
    exchange = call("auth.exchangeSilentAuthToken",
                    access_token=anon_token, token=parsed["token"],
                    uuid=parsed.get("uuid") or "")
    if not isinstance(exchange, dict):
        raise RuntimeError("VK silent session exchange failed")
    token = str(exchange.get("access_token") or "")
    user_id = exchange.get("user_id")
    if not TOKEN_PATTERN.fullmatch(token) or type(user_id) is not int:
        raise RuntimeError("VK did not issue a valid personal session")
    if parsed.get("vk_id") and str(user_id) != parsed["vk_id"]:
        raise RuntimeError("VK exchange returned another user, refusing session")
    if exchange.get("is_partial") or exchange.get("additional_signup_required"):
        raise RuntimeError("VK issued a partial session requiring additional verification")
    ttl = exchange.get("expires_in")
    if type(ttl) is not int or not 1 <= ttl <= 31_536_000:
        ttl = None
    if progress:
        progress("VK_PERSONAL_TOKEN_EXCHANGE_OK")
    return token, str(user_id), ttl


def verify_personal_reads(
    access_token: str, expected_user: str = "", *, call: Callable[..., Any] = vk_call,
    progress: Callable[[str], None] | None = None,
    test_peer_id: int | None = None,
) -> dict[str, object]:
    user = call("users.get", access_token=access_token)
    if not isinstance(user, list) or not user or not isinstance(user[0], dict):
        raise RuntimeError("VK users.get did not return a personal identity")
    actual = user[0].get("id")
    if type(actual) is not int or actual <= 0:
        raise RuntimeError("VK returned an invalid personal ID")
    if expected_user and str(actual) != expected_user:
        raise RuntimeError("VK session belongs to a different user")
    if progress:
        progress("VK_PERSONAL_IDENTITY_OK")
    conversations = call(
        "messages.getConversations", access_token=access_token,
        offset=0, count=5, extended=0,
    )
    if not isinstance(conversations, dict) or not isinstance(conversations.get("items"), list):
        raise RuntimeError("VK did not return personal conversations")
    items = conversations["items"]
    if progress:
        progress("VK_PERSONAL_DIALOGS_OK")
    peer = None
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (item.get("conversation") or {}).get("peer") or {}
        if type(key.get("id")) is int and key["id"]:
            peer = key["id"]
            break
    if test_peer_id is not None:
        if type(test_peer_id) is not int or test_peer_id == 0:
            raise ValueError("History test peer ID must be a nonzero integer")
        peer = test_peer_id
    if peer is None:
        raise RuntimeError("No personal conversation was found for history proof")
    history = call(
        "messages.getHistory", access_token=access_token,
        peer_id=peer, count=10,
    )
    if not isinstance(history, dict) or not isinstance(history.get("items"), list):
        raise RuntimeError("VK did not return the history of a personal chat")
    history_count = len(history["items"])
    if not history_count:
        raise RuntimeError("The selected personal VK dialog has no history")
    if progress:
        progress("VK_PERSONAL_HISTORY_OK")
    return {"identity_match": True, "dialog_count": conversations.get("count"),
            "sampled_chats": len(items), "sampled_history_messages": history_count,
            "history_requested": True}


def inspect_common_token(
    access_token: str, expected_vk_id: str, *, call: Callable[..., Any] = vk_call,
) -> dict[str, object]:
    """Optional Fast Messenger follow-up. Never treat common_token as refresh_token."""
    if not TOKEN_PATTERN.fullmatch(access_token):
        raise ValueError("Personal VK bearer is invalid")
    if not expected_vk_id.isdecimal():
        raise ValueError("Expected VK user ID is missing")
    response = call("auth.getExchangeToken", access_token=access_token)
    values = response.get("users_exchange_tokens") if isinstance(response, dict) else None
    if not isinstance(values, list):
        raise RuntimeError("VK did not return exchange-token account metadata")
    matches = [
        v for v in values if isinstance(v, dict) and
        v.get("user_id") == int(expected_vk_id)
    ]
    token_found = any(
        TOKEN_PATTERN.fullmatch(str(value.get("common_token") or "")) is not None
        for value in matches
    )
    # Do not expose or persist the opaque token: its intended lifetime and
    # semantics remain unknown until tested against actual VK behavior.
    return {
        "matching_account_found": bool(matches),
        "common_token_present": token_found,
        "is_refresh_token_verified": False,
    }


def save_temporary_session(token: str, expected: str, expires: int | None, path: Path):
    """Only with explicit CLI opt-in, restrict file and directory permissions."""
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink() or parent.stat().st_mode & 0o077:
        raise ValueError("Session directory must have mode 0700")
    if path.is_symlink():
        raise ValueError("Refusing symlink session destination")
    if path.exists():
        raise FileExistsError("Refusing to overwrite existing proof session")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as handle:
        # A proof session expires locally even if VK omits expiry data.
        ttl = min(expires if expires is not None else 3600, 86400)
        json.dump({"access_token": token, "user_id": expected,
                   "expires_at": time.time() + ttl}, handle)
        handle.flush()
        os.fsync(handle.fileno())


def main():
    parser = argparse.ArgumentParser(description="One-time personal VK server transport proof")
    parser.add_argument("--expected-vk-id", default="", help="Expected integer VK ID")
    parser.add_argument("--history-peer-id", type=int,
                        help="Known old personal conversation for history proof")
    parser.add_argument("--resume", type=Path, help="Load 0600 proof session after process restart")
    parser.add_argument("--save", type=Path, help="Explicitly opt in to save token in private 0700 dir")
    parser.add_argument("--inspect-common-token", action="store_true",
                        help="Optional metadata-only VK common_token probe after read proof")
    parser.add_argument("--anonymous-probe", action="store_true", help="No user token needed")
    parser.add_argument("--clear-session", type=Path, help="Discard one saved proof token")
    args = parser.parse_args()
    if args.expected_vk_id and not args.expected_vk_id.isdecimal():
        parser.error("Expected VK ID must be numeric")
    if args.clear_session:
        if args.clear_session.is_symlink() or args.clear_session.stat().st_mode & 0o077:
            raise ValueError("Refusing unsafe proof session")
        args.clear_session.unlink()
        print("PROOF_SESSION_DESTROYED", True)
        return 0
    if args.anonymous_probe:
        response = vk_call("auth.getAnonymToken", client_id=CLIENT_ID)
        print("anonymous_vk_auth_available:", bool(isinstance(response, dict) and response.get("token")))
        return 0
    if args.resume:
        if args.resume.is_symlink() or args.resume.stat().st_mode & 0o077:
            raise ValueError("Unsafe saved-session permissions")
        if args.resume.parent.stat().st_mode & 0o077:
            raise ValueError("Unsafe saved-session directory")
        item = json.loads(args.resume.read_text())
        if item.get("expires_at") is not None and item["expires_at"] < time.time():
            raise RuntimeError("VK session expired")
        token, actual, ttl = item["access_token"], str(item["user_id"]), None
    else:
        raw = getpass.getpass("Paste a fresh VK authorization result (input hidden): ")
        parsed = parse_login_result(raw)
        raw = ""
        token, actual, ttl = resolve_user_access(parsed, progress=print)
        parsed.clear()
    try:
        result = verify_personal_reads(
            token, args.expected_vk_id or actual, progress=print,
            test_peer_id=args.history_peer_id,
        )
        print("PERSONAL_VK_TRANSPORT_PROOF", json.dumps(result, ensure_ascii=False))
        if args.inspect_common_token:
            metadata = inspect_common_token(token, args.expected_vk_id or actual)
            print("VK_COMMON_TOKEN_METADATA", json.dumps(metadata))
        if args.save:
            save_temporary_session(token, actual, ttl, args.save)
            print("SESSION_SAVED_PRIVATELY", True, "TOKEN_NOT_PRINTED", True)
        return 0
    except VKRejected as error:
        print("VK_API_DENIED_METHOD", error.method, "ERROR_CODE", error.code)
        return 2
    finally:
        token = None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except VKRejected as error:
        print("VK_API_DENIED_METHOD", error.method, "ERROR_CODE", error.code)
        raise SystemExit(2)
    except (ValueError, RuntimeError, FileNotFoundError) as error:
        print("PROOF_FAILED:", str(error), file=sys.stderr)
        raise SystemExit(1)
