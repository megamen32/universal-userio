"""A source-bound Telegram reply: the answer names the message it answers."""
import io
import json

from connector_probe import reachable_probe
from universal_userio.adapters import TelegramQrHttpOutbox


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


def _outbox(runner):
    return TelegramQrHttpOutbox("http://telegram.invalid", "token-1", runner=runner, probe=reachable_probe)


def test_send_reply_answers_the_exact_source_message():
    calls = {}

    def recorder(**kwargs):
        calls.update(kwargs)
        return {"message_id": "78", "reply_to_id": "77", "thread_id": "70", "text": "answer", "out": True}

    outbox = _outbox(lambda *_args, **_kwargs: Response(b"{}"))
    outbox.reply_message = recorder
    receipt = outbox.send_reply(chat="", body="answer", draft_id="draft_1", chat_id="900000300",
        account_ref="telegram:900000101", slot="account-7",
        reply_to={"message_id": "77", "expected_text": "source", "thread_id": "70"})
    assert calls == {"chat_id": "900000300", "account_ref": "telegram:900000101",
        "message_id": "77", "thread_id": "70", "expected_text": "source",
        "text": "answer", "request_id": "draft_1"}
    assert receipt == "telegram-qr:account-7:78:draft_1:reply_to:77:thread:70"


def test_the_reply_reaches_the_connector_bound_to_its_source():
    captured = {}

    def runner(request, **_kwargs):
        captured["request"] = request
        payload = json.loads(request.data)
        return Response(json.dumps({
            "ok": True, "account_id": payload["account_id"], "peer_id": payload["chat_id"],
            "message_id": "78", "reply_to_id": payload["reply_to_id"],
            "thread_id": payload["thread_id"], "text": payload["body"], "date": 123, "out": True,
        }).encode())

    outbox = _outbox(runner)
    receipt = outbox.send_reply(chat="", body="answer", draft_id="draft_1", chat_id="900000300",
        account_ref="telegram:900000101",
        reply_to={"message_id": "77", "expected_text": "source", "thread_id": "70"})
    assert json.loads(captured["request"].data) == {
        "account_id": "telegram:900000101", "chat_id": "900000300",
        "reply_to_id": "77", "thread_id": "70", "expected_text": "source",
        "body": "answer", "request_id": "draft_1",
    }
    assert receipt == "telegram-qr:?:78:draft_1:reply_to:77:thread:70"


def test_a_broken_source_binding_is_refused_before_the_connector():
    called = []

    def runner(*_args, **_kwargs):
        called.append(True)
        return Response(b"{}")

    outbox = _outbox(runner)
    broken = (["77"], {"message_id": "77"}, {"message_id": 77, "expected_text": "source"},
        {"message_id": "77", "expected_text": "source", "chat_id": "900000300"},
        {"message_id": "77", "expected_text": "source", "thread_id": 70})
    for binding in broken:
        try:
            outbox.send_reply(chat="", body="answer", draft_id="d", chat_id="900000300",
                account_ref="telegram:900000101", reply_to=binding)
        except ValueError:
            pass
        else:
            raise AssertionError(f"a broken reply binding must be refused: {binding!r}")
    assert called == []


def test_a_plain_send_still_asks_for_a_chat():
    outbox = _outbox(lambda *_args, **_kwargs: Response(b"{}"))
    try:
        outbox.send_reply(chat="", body="answer", draft_id="d")
    except ValueError:
        pass
    else:
        raise AssertionError("a plain send without a chat must be refused")


def test_a_plain_send_is_unchanged():
    captured = {}

    def runner(request, **_kwargs):
        captured["request"] = request
        return Response(json.dumps({"ok": True, "slot": "account-7", "message_id": "55"}).encode())

    outbox = _outbox(runner)
    receipt = outbox.send_reply(chat="Секретарь Никиты Р", body="канарейка", draft_id="draft_1",
        chat_id="900000103", account_ref="telegram:900000101", slot="account-7")
    assert json.loads(captured["request"].data) == {"chat": "Секретарь Никиты Р",
        "chat_id": "900000103", "account_id": "telegram:900000101", "slot": "account-7",
        "body": "канарейка", "attempt_id": "draft_1"}
    assert receipt == "telegram-qr:account-7:55:draft_1"


def test_a_repeated_reply_is_accepted_without_a_body():
    def runner(request, **_kwargs):
        payload = json.loads(request.data)
        return Response(json.dumps({
            "ok": True, "duplicate": True, "account_id": payload["account_id"],
            "peer_id": payload["chat_id"], "message_id": "78",
            "reply_to_id": payload["reply_to_id"], "thread_id": payload["thread_id"],
            "date": 1700000100,
        }).encode())

    outbox = _outbox(runner)
    result = outbox.reply_message(chat_id="900000300", account_ref="telegram:900000101",
        message_id="77", thread_id="70", expected_text="source", text="answer", request_id="draft_1")
    assert result["duplicate"] is True
    assert result["message_id"] == "78"


def test_a_repeated_reply_must_still_name_the_same_source():
    def runner(*_args, **_kwargs):
        return Response(json.dumps({
            "ok": True, "duplicate": True, "account_id": "telegram:900000101",
            "peer_id": "900000300", "message_id": "78", "reply_to_id": "99",
            "thread_id": "70", "date": 1700000100,
        }).encode())

    outbox = _outbox(runner)
    try:
        outbox.reply_message(chat_id="900000300", account_ref="telegram:900000101",
            message_id="77", thread_id="70", expected_text="source", text="answer", request_id="d")
    except RuntimeError:
        pass
    else:
        raise AssertionError("a repeated reply under another source must be refused")
