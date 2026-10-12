"""Per-user VK *community* messages, using documented read-only VK API.

These are NOT the personal inbox of the linked VK ID. Each bearer belongs to a
community admin and may only read that community's messages. All routes below
are read-only: never send, delete, edit or mark messages as read.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

_API_URL = "https://api.vk.com/method/"
_API_VERSION = "5.199"
_READ_METHODS = frozenset({"messages.getConversations", "messages.getHistory"})


class VKCommunityPermissionDenied(ValueError):
    """The linked community token cannot access messages."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("VK API redirects are forbidden for token-bearing requests")


class VKCommunityReader:
    """Narrow VK community message client with testable HTTP boundary."""

    def __init__(
        self, token: str, group_id: int, *, opener: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(token, str) or not 10 <= len(token) <= 4096:
            raise ValueError("укажите действительный токен сообщества")
        if type(group_id) is not int or group_id <= 0 or group_id > 2**53:
            raise ValueError("неверный ID сообщества")
        self.token = token
        self.group_id = group_id
        self._opener = opener or urllib.request.build_opener(_NoRedirect).open
        self._last_call = 0.0

    def _call(self, method: str, **args: Any) -> dict[str, Any]:
        if method not in _READ_METHODS:
            raise ValueError("VK community transport allows read-only methods only")
        elapsed = time.monotonic() - self._last_call
        if self._last_call and elapsed < 0.38:
            time.sleep(0.38 - elapsed)
        self._last_call = time.monotonic()
        payload = urllib.parse.urlencode({
            "access_token": self.token, "group_id": self.group_id,
            "v": _API_VERSION, **args,
        }).encode("utf-8")
        request = urllib.request.Request(
            _API_URL + method, method="POST", data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
        )
        try:
            with self._opener(request, timeout=12) as response:
                body = response.read(2_000_001)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError) as error:
            raise RuntimeError("VK API недоступен") from error
        if len(body) > 2_000_000:
            raise ValueError("слишком большой ответ VK")
        try:
            data = json.loads(body)
        except (UnicodeError, ValueError) as error:
            raise RuntimeError("VK API ответил неверным форматом") from error
        if not isinstance(data, dict):
            raise RuntimeError("неверный ответ VK")
        if isinstance(data.get("error"), dict):
            code = data["error"].get("error_code")
            if code in (5, 7, 15, 20, 27, 28):
                raise VKCommunityPermissionDenied(
                    "токен не разрешает читать сообщения указанного сообщества"
                )
            if code in (6, 29):
                raise RuntimeError("VK ограничил частоту обращений")
            raise RuntimeError("VK API отказал в чтении сообщений")
        result = data.get("response")
        if not isinstance(result, dict):
            raise RuntimeError("VK API не вернул данные")
        return result

    def conversations(self, *, offset: int = 0, count: int = 25) -> dict[str, Any]:
        if (type(offset) is not int or offset < 0 or
                type(count) is not int or not 1 <= count <= 100):
            raise ValueError("некорректная страница диалогов")
        response = self._call(
            "messages.getConversations", offset=offset, count=count, extended=1,
        )
        if not isinstance(response.get("items"), list):
            raise RuntimeError("VK не вернул диалоги")
        return response

    def history(
        self, peer_id: int, *, offset: int = 0, count: int = 35,
    ) -> list[dict[str, Any]]:
        if (type(peer_id) is not int or peer_id == 0 or
                type(offset) is not int or offset < 0 or
                type(count) is not int or not 1 <= count <= 100):
            raise ValueError("неверный запрос истории")
        response = self._call(
            "messages.getHistory", peer_id=peer_id, offset=offset, count=count,
        )
        items = response.get("items")
        if not isinstance(items, list):
            raise RuntimeError("VK не вернул историю")
        return [m for m in items if isinstance(m, dict)]
