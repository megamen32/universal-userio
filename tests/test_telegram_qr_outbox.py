"""Telegram delivery through the telegram-qr connector HTTP endpoint."""
import io
import json
import urllib.error

from universal_userio.adapters import TelegramQrHttpOutbox


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


def _ok_run(request, **_kwargs):
    captured["request"] = request
    return _Response(json.dumps({"ok": True, "slot": "account-7", "message_id": "55"}).encode())


captured: dict = {}


def test_send_reply_posts_chat_and_body_and_returns_receipt():
    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095/", "token-1", runner=_ok_run)
    receipt = outbox.send_reply(chat="Секретарь Никиты Р", body="канарейка", draft_id="draft_1", chat_id="8810909089")
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:18095/send"
    assert request.get_header("Authorization") == "Bearer token-1"
    assert json.loads(request.data.decode()) == {"chat": "Секретарь Никиты Р", "chat_id": "8810909089", "body": "канарейка"}
    assert receipt == "telegram-qr:account-7:55:draft_1"


def test_send_reply_requires_chat_and_body():
    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token-1", runner=_ok_run)
    try:
        outbox.send_reply(chat="", body="x", draft_id="d")
    except ValueError:
        pass
    else:
        raise AssertionError("empty chat must be rejected")


def test_send_reply_surfaces_http_error_detail():
    def fail(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO(b'{"error": "no chat"}'))

    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token-1", runner=fail)
    try:
        outbox.send_reply(chat="ghost", body="x", draft_id="d")
    except RuntimeError as error:
        assert "404" in str(error)
    else:
        raise AssertionError("HTTP error must raise RuntimeError")
