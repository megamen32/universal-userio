"""VK personal-messages API, limited to consented user tokens.

Use only a user-authorized VK Messenger access token that has been independently
verified against messages.getConversations and messages.getHistory. It sends
no messages. The separate client sends only an explicitly approved message;
neither reader nor sender marks conversations read.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable
from ..contracts import DeliveryNotSubmittedError


class VKPermissionDenied(ValueError):
    """This VK token cannot access personal messages."""


class VKPersonalReader:
    API_BASE = "https://api.vk.ru/method/"
    API_VERSION = "5.199"

    def __init__(self, access_token: str, *, opener: Callable[..., Any] | None = None):
        if not isinstance(access_token, str) or not 10 <= len(access_token) <= 4096:
            raise ValueError("VK не выдал пригодный токен")
        self._token = access_token
        self._opener = opener or urllib.request.build_opener(_RejectRedirect).open
        self._last_call = 0.0

    def call(self, method: str, **kwargs: object) -> Any:
        if method not in {"users.get", "messages.getConversations", "messages.getHistory", "messages.getLongPollServer", "messages.getById"}:
            raise ValueError("VK method not allowed in read-only connector")
        return self._request(method, kwargs)

    def _request(self, method: str, kwargs: dict, *, mutation=False) -> Any:
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
            if mutation and type(code) is int:
                raise DeliveryNotSubmittedError(f"VK не отправил сообщение (код {code}). Проверьте доступ аккаунта к переписке.")
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

    def longpoll_server(self) -> dict[str, Any]:
        result = self.call("messages.getLongPollServer", need_pts=1, lp_version=10)
        if not isinstance(result, dict):
            raise RuntimeError("VK не вернул параметры User Long Poll")
        if not all(isinstance(result.get(k), (str, int)) for k in ("server", "key", "ts")):
            raise RuntimeError("VK Long Poll ответ некорректен")
        return result

    def message(self, message_id: int) -> dict[str, Any]:
        if type(message_id) is not int or message_id<=0:
            raise ValueError('VK exact message ID required')
        result=self.call('messages.getById',message_ids=message_id)
        rows=result.get('items') if isinstance(result,dict) else None
        row=next((r for r in rows or [] if isinstance(r,dict) and r.get('id')==message_id),None)
        if row is None: raise ValueError('VK не вернул отправленное сообщение')
        return row


class VKPersonalClient(VKPersonalReader):
    def send(self, peer_id: int, text: str, random_id: int) -> str:
        if type(peer_id) is not int or peer_id==0:
            raise ValueError('VK exact peer ID required')
        if not isinstance(text,str) or not text.strip() or len(text)>4096:
            raise ValueError('Сообщение VK должно содержать от 1 до 4096 символов')
        if type(random_id) is not int or not 0<random_id<2**31:
            raise ValueError('VK stable random ID required')
        # VKCOM schema: user-token messages.send, random_id deduplicates sends.
        result=self._request('messages.send',{'peer_id':peer_id,'message':text,'random_id':random_id},mutation=True)
        if type(result) is not int or result<=0:
            raise RuntimeError('VK не подтвердил отправку. Не повторяйте сообщение; обновите диалог.')
        return str(result)


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("VK API must not redirect access tokens")
