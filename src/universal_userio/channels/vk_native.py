"""Read-only VK personal-messages API, limited to consented user tokens.

Unlike VK ID profile authentication, this transport must prove actual access to
messages.getConversations before any account may be marked readable. It sends
no messages and never marks conversations read.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable


class VKPermissionDenied(ValueError):
    """This VK token cannot access personal messages."""


class VKNativeReader:
    API_BASE = "https://api.vk.ru/method/"
    API_VERSION = "5.199"

    def __init__(self, access_token: str, *, opener: Callable[..., Any] | None = None):
        if not isinstance(access_token, str) or not 10 <= len(access_token) <= 4096:
            raise ValueError("VK не выдал пригодный токен")
        self._token = access_token
        self._opener = opener or urllib.request.build_opener(_RejectRedirect).open
        self._last_call = 0.0

    def call(self, method: str, **kwargs: object) -> Any:
        if method not in {"users.get", "messages.getConversations", "messages.getHistory"}:
            raise ValueError("VK method not allowed in read-only connector")
        # Respect a small per-session VK API budget without holding the
        # shared UserIO database lock while waiting.
        elapsed = time.monotonic() - self._last_call
        if elapsed < 0.36 and self._last_call:
            time.sleep(0.36 - elapsed)
        self._last_call = time.monotonic()
        form = urllib.parse.urlencode({
            "access_token": self._token, "v": self.API_VERSION, **kwargs,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.API_BASE + method, data=form, method="POST",
            headers={"Accept": "application/json",
                     "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with self._opener(request, timeout=12) as response:
                body = response.read(2_000_001)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise RuntimeError("VK API временно недоступен") from exc
        if len(body) > 2_000_000:
            raise ValueError("слишком большой ответ VK")
        try:
            response = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise RuntimeError("VK API вернул некорректный ответ") from exc
        if not isinstance(response, dict):
            raise RuntimeError("VK API вернул некорректный ответ")
        error = response.get("error")
        if isinstance(error, dict):
            code = error.get("error_code")
            if code in (5, 7, 15, 20, 27, 28):
                raise VKPermissionDenied(
                    "VK не выдал доступ к личным сообщениям: нужен "
                    "пользовательский токен с разрешением messages"
                )
            if code in (6, 29):
                raise RuntimeError("VK API ограничил частоту обращений")
            raise RuntimeError("VK API отказал в чтении")
        if "response" not in response:
            raise RuntimeError("VK API не вернул данные")
        return response["response"]

    def identity(self) -> int:
        result = self.call("users.get")
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise RuntimeError("VK не подтвердил пользователя")
        actual = result[0].get("id")
        if type(actual) is not int or actual <= 0:
            raise RuntimeError("VK вернул неверный ID пользователя")
        return actual

    def conversations(self, *, offset: int = 0, count: int = 50) -> dict[str, Any]:
        if type(offset) is not int or offset < 0 or type(count) is not int or not 1 <= count <= 100:
            raise ValueError("VK dialog page is invalid")
        result = self.call("messages.getConversations", offset=offset, count=count, extended=1)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("VK не вернул список диалогов")
        return result

    def history(self, peer_id: int, *, count: int = 30, offset: int = 0) -> list[dict[str, Any]]:
        if (type(peer_id) is not int or peer_id == 0 or type(count) is not int
                or not 1 <= count <= 100 or type(offset) is not int or offset < 0):
            raise ValueError("VK history request is invalid")
        result = self.call("messages.getHistory", peer_id=peer_id, count=count, offset=offset)
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise RuntimeError("VK не вернул историю диалога")
        return [r for r in result["items"] if isinstance(r, dict)]


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("VK API must not redirect access tokens")
