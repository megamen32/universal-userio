import io
import json

from universal_userio.adapters import TelegramQrHttpOutbox


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


def test_reply_posts_exact_account_peer_source_and_thread_and_checks_receipt():
    captured = {}

    def runner(request, **_kwargs):
        captured["request"] = request
        payload = json.loads(request.data)
        return Response(json.dumps({
            "ok": True, "account_id": payload["account_id"], "peer_id": payload["chat_id"],
            "message_id": "78", "reply_to_id": payload["reply_to_id"],
            "thread_id": payload["thread_id"], "text": payload["body"], "date": 123, "out": True,
        }).encode())

    outbox = TelegramQrHttpOutbox("http://telegram.invalid", "token-1", runner=runner)
    result = outbox.reply_message(chat_id="900000300", account_ref="telegram:900000101",
        message_id="77", thread_id="70", expected_text="source", text="answer", request_id="action-1")
    assert captured["request"].full_url == "http://telegram.invalid/send"
    assert json.loads(captured["request"].data) == {
        "account_id": "telegram:900000101", "chat_id": "900000300",
        "reply_to_id": "77", "thread_id": "70", "expected_text": "source",
        "body": "answer", "request_id": "action-1",
    }
    assert result["message_id"] == "78"


def test_reply_refuses_bad_native_ids_before_http():
    called = False

    def runner(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid reply must not reach connector")

    outbox = TelegramQrHttpOutbox("http://telegram.invalid", "token-1", runner=runner)
    try:
        outbox.reply_message(chat_id="900000300", account_ref="telegram:900000101",
            message_id="99999999999", thread_id="", expected_text="source", text="answer", request_id="a")
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range source message ids must be rejected")
    assert not called
