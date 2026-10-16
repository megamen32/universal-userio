"""Telegram delivery through the telegram-qr connector HTTP endpoint."""
import io
import json
import urllib.error

from universal_userio.adapters import TelegramConnectorRefused, TelegramQrHttpOutbox
from universal_userio.contracts import DeliveryNotSubmittedError


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
    receipt = outbox.send_reply(chat="Секретарь Никиты Р", body="канарейка", draft_id="draft_1", chat_id="900000103", account_ref="telegram:900000101")
    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:18095/send"
    assert request.get_header("Authorization") == "Bearer token-1"
    assert json.loads(request.data.decode()) == {"chat": "Секретарь Никиты Р", "chat_id": "900000103", "account_id": "telegram:900000101", "slot": "", "body": "канарейка"}
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


def _refuse(status: int, body: bytes):
    def fail(request, **_kwargs):
        raise urllib.error.HTTPError(request.full_url, status, "refused", {}, io.BytesIO(body))
    return TelegramQrHttpOutbox("http://127.0.0.1:18095", "token-1", runner=fail)


def test_offline_account_is_a_proven_non_submission():
    # The connector answers before touching Telegram, so the durable claim
    # may be released and a duplicate message is impossible.
    outbox = _refuse(404, json.dumps({"error": "account telegram:540308572 is not connected",
        "submitted": False, "code": "account_not_connected"}).encode())
    try:
        outbox.send_reply(chat="Менеджер", body="x", draft_id="d", account_ref="telegram:540308572")
    except TelegramConnectorRefused as error:
        assert isinstance(error, DeliveryNotSubmittedError)
        assert error.connector_code == "account_not_connected"
        assert error.http_status == 404
        assert "не отправлено" in str(error)
    else:
        raise AssertionError("an offline account must not be an uncertain send")


def test_revoked_session_tells_the_owner_to_reconnect():
    outbox = _refuse(404, json.dumps({"error": "account telegram:540308572 is not connected",
        "submitted": False, "code": "account_relogin_required"}).encode())
    try:
        outbox.send_reply(chat="Менеджер", body="x", draft_id="d", account_ref="telegram:540308572")
    except TelegramConnectorRefused as error:
        assert error.connector_code == "account_relogin_required"
        assert "Переподключите" in str(error)
    else:
        raise AssertionError("a revoked session must be reported as not submitted")


def test_server_side_failure_stays_uncertain():
    # 5xx can arrive with the message already on the wire: never release.
    outbox = _refuse(502, json.dumps({"error": "send failed", "submitted": "unknown"}).encode())
    try:
        outbox.send_reply(chat="Менеджер", body="x", draft_id="d", account_ref="telegram:540308572")
    except DeliveryNotSubmittedError:
        raise AssertionError("a 502 answer must stay an uncertain send")
    except RuntimeError as error:
        assert "502" in str(error)
    else:
        raise AssertionError("connector failure must raise RuntimeError")


def test_unreachable_connector_stays_uncertain():
    def fail(request, **_kwargs):
        raise urllib.error.URLError("connection refused")

    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token-1", runner=fail)
    try:
        outbox.send_reply(chat="Менеджер", body="x", draft_id="d")
    except DeliveryNotSubmittedError:
        raise AssertionError("an unreachable connector must stay an uncertain send")
    except RuntimeError as error:
        assert "unreachable" in str(error)
    else:
        raise AssertionError("unreachable connector must raise RuntimeError")


def test_pinned_slot_is_sent_with_the_account():
    outbox = TelegramQrHttpOutbox("http://127.0.0.1:18095", "token-1", runner=_ok_run)
    outbox.send_reply(chat="Менеджер", chat_id="8810909089", body="канарейка", draft_id="d",
        account_ref="telegram:540308572", slot="account-1")
    sent = json.loads(captured["request"].data.decode())
    assert sent["account_id"] == "telegram:540308572"
    assert sent["slot"] == "account-1"
