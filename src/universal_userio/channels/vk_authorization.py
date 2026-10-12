"""Explicit owner-confirmed import of a VK Messenger authorization result.

This is not an automatic OAuth callback: the authenticated user supplies the
result to their own private form. Provider identity and message permissions
are verified separately before enrollment. No browser session is retained.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from .vk_personal import _RejectRedirect

_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{10,4096}$")
_CLIENT_ID = "51453752"  # Authorization client, not UserIO's VK ID application.


def _exchange_call(method: str, **fields: object) -> Any:
    if method not in {"auth.getAnonymToken", "auth.exchangeSilentAuthToken"}:
        raise ValueError("unsupported VK authorization method")
    request = urllib.request.Request(
        "https://api.vk.ru/method/" + method,
        data=urllib.parse.urlencode({"v": "5.263", **fields}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.build_opener(_RejectRedirect).open(request, timeout=12) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("oversized response")
        result = json.loads(raw)
    except (OSError, ValueError) as error:
        raise ValueError("Не удалось завершить авторизацию VK. Повторите вход.") from error
    if not isinstance(result, dict) or not isinstance(result.get("response"), dict):
        # Provider response/error strings may contain secrets. Never forward them.
        error = result.get("error") if isinstance(result, dict) else None
        code = error.get("error_code") if isinstance(error, dict) else None
        detail = f" (код {code})" if type(code) is int and 0 < code < 10000 else ""
        stage = "обмен временного разрешения" if method == "auth.exchangeSilentAuthToken" else "начало входа"
        raise ValueError(f"VK не подтвердил {stage}{detail}. Получите новый результат входа.")
    return result["response"]


def resolve_authorization(value: str, *, call: Callable[..., Any] = _exchange_call
                          ) -> tuple[str, str, int | None]:
    """Return the final bearer, asserted provider ID and final bearer lifetime."""
    if not isinstance(value, str) or len(value) > 12_000:
        raise ValueError("Некорректный результат авторизации VK")
    value = value.strip()
    if _TOKEN.fullmatch(value):
        return value, "", None
    try:
        url = urllib.parse.urlsplit(value)
        valid = (url.scheme == "https" and url.hostname in {"oauth.vk.ru", "oauth.vk.com"}
                 and url.path == "/blank.html" and url.port in (None, 443)
                 and not url.username and not url.password and not url.query)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Вставьте итоговый адрес авторизации VK")
    fragment = urllib.parse.parse_qs(url.fragment, keep_blank_values=True)
    if any(len(v) != 1 for v in fragment.values()) or "error" in fragment:
        raise ValueError("VK не подтвердил вход")
    if "access_token" in fragment and "payload" not in fragment:
        token = fragment["access_token"][0]
        uid = fragment.get("user_id", [""])[0]
        if not _TOKEN.fullmatch(token) or (uid and (not uid.isdecimal() or int(uid) <= 0)):
            raise ValueError("Некорректный токен VK")
        expires = fragment.get("expires_in", [""])[0]
        ttl = int(expires) if expires.isdecimal() and 0 < int(expires) <= 31_536_000 else None
        return token, uid, ttl
    if "payload" not in fragment or "access_token" in fragment:
        raise ValueError("VK не вернул результат авторизации")
    try:
        data = json.loads(fragment["payload"][0])
    except ValueError as error:
        raise ValueError("Некорректный результат авторизации VK") from error
    if not isinstance(data, dict):
        raise ValueError("Некорректный результат авторизации VK")
    token, ttl, user = data.get("token"), data.get("ttl"), data.get("user")
    uid = user.get("id") if isinstance(user, dict) else None
    uuid = data.get("uuid", "")
    if (data.get("type") != "silent_token" or not isinstance(token, str)
            or not _TOKEN.fullmatch(token) or type(uid) is not int or uid <= 0
            or type(ttl) is not int or not 1 <= ttl <= 86400
            or not isinstance(uuid, str) or len(uuid) > 256 or "\n" in uuid):
        raise ValueError("Некорректный результат авторизации VK")
    if not uuid:
        raise ValueError("Этот адрес входа VK неполный. Откройте «Войти через VK Мессенджер» в UserIO заново.")
    anon = call("auth.getAnonymToken", client_id=_CLIENT_ID)
    anonymous = anon.get("token") if isinstance(anon, dict) else None
    if not isinstance(anonymous, str) or not _TOKEN.fullmatch(anonymous):
        raise ValueError("VK не выдал сессию авторизации")
    result = call("auth.exchangeSilentAuthToken", access_token=anonymous, token=token, uuid=uuid)
    final = result.get("access_token") if isinstance(result, dict) else None
    if (not isinstance(final, str) or not _TOKEN.fullmatch(final)
            or type(result.get("user_id")) is not int or result["user_id"] != uid
            or result.get("is_partial") or result.get("additional_signup_required")):
        raise ValueError("VK не подтвердил полную сессию этого аккаунта")
    lifetime = result.get("expires_in")
    if type(lifetime) is not int or not 1 <= lifetime <= 31_536_000:
        lifetime = None
    return final, str(uid), lifetime
