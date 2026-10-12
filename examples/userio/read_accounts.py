#!/usr/bin/env python3
"""Log in interactively and list the current User.io user's connected accounts.

No credentials are saved, printed, or put into command-line arguments.
This example never sends messages and does not read conversation contents.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from typing import Any

MAX_RESPONSE_BYTES = 1024 * 1024


def validate_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Адрес должен содержать только схему, хост и необязательный порт.")
    if parsed.path not in {"", "/"}:
        raise ValueError("Укажите адрес сервиса без пути /mcp или /login.")
    if parsed.scheme != "https":
        if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Для удалённого сервиса требуется HTTPS; HTTP разрешён только для localhost.")
    return value.rstrip("/")


def request_json(base_url: str, path: str, *, payload: dict[str, str] | None = None,
                 token: str | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = Request(base_url + path, data=body, headers=headers,
                      method="POST" if payload is not None else "GET")
    # Do not follow redirects for requests containing credentials or a token.
    from urllib.request import HTTPRedirectHandler, build_opener
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            return None
    with build_opener(NoRedirect()).open(request, timeout=15) as response:
        raw = response.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("Слишком большой ответ сервера.")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Сервер вернул неожиданный формат ответа.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://msg.bezrabotnyi.com")
    args = parser.parse_args()
    try:
        base_url = validate_base_url(args.base_url)
        username = input("Имя пользователя User.io: ").strip()
        password = getpass.getpass("Пароль User.io: ")
        if not username or not password:
            raise ValueError("Нужны имя пользователя и пароль.")
        login = request_json(base_url, "/auth/login", payload={
            "username": username, "password": password,
        })
        password = ""
        token = login.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("Сервер не выдал персональный токен.")
        result = request_json(base_url, "/v1/accounts", token=token)
        token = ""
        accounts = result.get("accounts")
        if not isinstance(accounts, list):
            raise ValueError("В ответе нет списка аккаунтов.")
        print(f"Подключено аккаунтов: {len(accounts)}")
        for account in accounts:
            if isinstance(account, dict):
                # Show only selected public metadata, never the raw API response.
                print(json.dumps({key: account.get(key) for key in
                    ("id", "provider", "display_name", "enabled", "capabilities")},
                    ensure_ascii=False))
        return 0
    except HTTPError as error:
        reasons = {401: "Проверьте имя пользователя и пароль.",
                   403: "У пользователя нет необходимых прав."}
        print(f"HTTP {error.code}. " + reasons.get(error.code,
              "Проверьте адрес и состояние сервиса; запрос автоматически не повторяется."),
              file=sys.stderr)
    except (URLError, TimeoutError, OSError):
        print("Не удалось связаться с User.io. Проверьте сеть и адрес сервиса.", file=sys.stderr)
    except (ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
    except (KeyboardInterrupt, EOFError):
        print("\nОтменено.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
