import json

import pytest

from universal_userio.channels.gmail import (
    GmailMessage as ChannelGmailMessage,
    HimalayaReader,
    html_to_accessible_text,
    himalaya_mailbox_addresses,
    readable_message_body,
)
from universal_userio.gmail_ingress import (
    GmailMessage,
    NoticePlaceEmailNotifier,
    UserIOIngressClient,
    poll_once,
)

class Reader:
    def __init__(self): self.cursors=[]
    def poll(self,cursor,*,limit,known_message_ids=frozenset()):
        self.cursors.append(cursor); return ([GmailMessage("m2","2","a@example.com","hello")],"m2")
class Sink:
    def __init__(self): self.items=[]; self.cursors={"gmail":"m1"}
    def cursor(self,source): return self.cursors.get(source)
    def recent_message_ids(self,source): return frozenset({"m1"})
    def set_cursor(self,source,cursor): self.cursors[source]=cursor
    def send(self,account,message): self.items.append((account,message.message_id))

def test_gmail_ingress_uses_userio_cursor_api():
    reader=Reader(); sink=Sink()
    assert poll_once(sink,{'gmail':reader},limit=10)==1
    assert reader.cursors==['m1']; assert sink.items==[('gmail','m2')]
    assert sink.cursors['gmail']=='m2'


def test_missing_cursor_recovers_from_recent_durable_message() -> None:
    reader = HimalayaReader("himalaya", "careviolan", snapshot_size=10)
    reader._run = lambda *_args: {"envelopes": [
        {"id": "3", "message-id": "m3"},
        {"id": "2", "message-id": "m2"},
        {"id": "1", "message-id": "durable-anchor"},
    ]}
    reader._read = lambda env: GmailMessage(str(env["message-id"]), str(env["id"]), "sender", "body")

    messages, cursor = reader.poll(
        "deleted-cursor", limit=10, known_message_ids=frozenset({"durable-anchor"}),
    )

    assert [message.message_id for message in messages] == ["m2", "m3"]
    assert cursor == "m3"


def test_reader_prefers_plain_text_and_keeps_html_as_accessible_fallback() -> None:
    reader = HimalayaReader("himalaya", "yandex", mailbox_address="owner@yandex.ru")
    envelope = {
        "id": "42", "message-id": "provider-id",
        "from": [{"email": "no-reply@cloud.yandex.ru"}],
        "subject": "Платёж за облако",
    }
    reader._run = lambda *_args: {
        "parts": [
            {"body": {"Html": "<html><body><h1>HTML версия</h1></body></html>"}},
            {"body": {"Text": "Обычный текст письма"}},
        ],
        "html_body": [0], "text_body": [1],
    }

    message = reader._read(envelope)

    assert message.body == "Обычный текст письма"
    assert readable_message_body(message) == (
        "Почтовый аккаунт: owner@yandex.ru\nТема: Платёж за облако\n\nОбычный текст письма"
    )


def test_html_fallback_exposes_meaningful_text_not_markup_or_hidden_noise() -> None:
    html = """
    <!doctype html><html><head><meta charset="utf-8"><style>.cta { color: orange }</style></head>
    <body><div style="display:none">tracking preview</div>
    <h1>Платёж не прошёл</h1><p>Пополните баланс до 10 октября.</p>
    <a href="https://phishing.invalid/track">Открыть консоль</a>
    <img alt="Yandex Cloud" src="pixel.gif"><script>alert(1)</script></body></html>
    """

    text = html_to_accessible_text(html)

    assert text.splitlines() == [
        "Платёж не прошёл", "Пополните баланс до 10 октября.",
        "Открыть консоль", "Yandex Cloud",
    ]
    assert "<" not in text
    assert "tracking" not in text
    assert "phishing.invalid" not in text


def test_html_fallback_preserves_repeated_lines_and_skips_hidden_content() -> None:
    html = (
        '<p>Подтверждено</p><p>Подтверждено</p>'
        '<span hidden>Тайный блок</span>'
        '<span style="display:\t none">Скрытый блок</span>'
        '<span aria-hidden=" true ">Шум</span>'
    )

    assert html_to_accessible_text(html).splitlines() == ["Подтверждено", "Подтверждено"]


def test_reader_converts_html_only_message_to_accessible_text() -> None:
    reader = HimalayaReader("himalaya", "yandex")
    reader._run = lambda *_args: {
        "parts": [{"body": {"Html": "<table><tr><td>Счёт</td><td>1 200 ₽</td></tr></table>"}}],
        "html_body": [0], "text_body": [],
    }

    message = reader._read({"id": "9", "message-id": "m9", "subject": "Баланс"})

    assert message.body.splitlines() == ["Счёт", "1 200 ₽"]
    assert readable_message_body(ChannelGmailMessage("m9", "9", "sender", message.body, "Баланс", "owner@yandex.ru")) == (
        "Почтовый аккаунт: owner@yandex.ru\nТема: Баланс\n\nСчёт\n1 200 ₽"
    )


def test_reader_converts_html_variant_even_when_listed_as_text_body() -> None:
    reader = HimalayaReader("himalaya", "yandex")
    reader._run = lambda *_args: {
        "parts": [{"body": {"Html": "<p>Счёт оплачен</p>"}}],
        "text_body": [0], "html_body": [],
    }

    assert reader._read({"id": "10", "subject": "Оплата"}).body == "Счёт оплачен"


def test_empty_html_body_does_not_duplicate_subject_in_ingress() -> None:
    reader = HimalayaReader("himalaya", "yandex", mailbox_address="owner@yandex.ru")
    reader._run = lambda *_args: {
        "parts": [{"body": {"Html": '<span hidden>tracking text</span>'}}],
        "text_body": [], "html_body": [0],
    }
    message = reader._read({"id": "11", "subject": "Оплата"})
    sent = []
    client = UserIOIngressClient("http://userio.invalid", "token")
    client._json = lambda request: sent.append(json.loads(request.data)) or {}

    client.send("yandex", message)

    assert message.body == ""
    assert sent[0]["message"]["body"] == (
        "Почтовый аккаунт: owner@yandex.ru\nТема: Оплата\n\nПисьмо без текстового содержимого."
    )


def test_mailbox_address_comes_from_exact_himalaya_account(tmp_path) -> None:
    config = tmp_path / "himalaya.toml"
    config.write_text(
        '[accounts.work]\nemail = "owner@example.com"\n'
        'imap.sasl.plain.username = "owner@example.com"\n'
        '[accounts.private]\nemail = "private@example.org"\n',
        encoding="utf-8",
    )

    assert himalaya_mailbox_addresses(config, ("work", "private")) == {
        "work": "owner@example.com", "private": "private@example.org",
    }
    assert himalaya_mailbox_addresses(config, ("missing",)) == {"missing": ""}


def test_ingress_uses_stable_account_ref_when_exact_address_is_unavailable() -> None:
    client = UserIOIngressClient("http://userio.invalid", "token")
    sent = []
    client._json = lambda request: sent.append(json.loads(request.data)) or {}

    client.send("gmail", GmailMessage("m1", "1", "sender", "body", "Subject"))

    assert sent[0]["message"]["body"] == (
        "Почтовый аккаунт: account_ref:gmail\nТема: Subject\n\nbody"
    )


def test_one_broken_account_does_not_block_other_accounts() -> None:
    class BrokenReader:
        def poll(self, *_args, **_kwargs):
            raise RuntimeError("broken account")

    sink = Sink()
    with pytest.raises(RuntimeError, match="careviolan"):
        poll_once(sink, {"careviolan": BrokenReader(), "gmail": Reader()}, limit=10)

    assert sink.items == [("gmail", "m2")]
    assert sink.cursors["gmail"] == "m2"


def test_noticeplace_notification_is_human_readable_and_idempotent() -> None:
    calls = []

    class Response:
        status = 202
        def __enter__(self): return self
        def __exit__(self, *_args): return None

    def opener(request, *, timeout):
        calls.append((request, timeout))
        return Response()

    notifier = NoticePlaceEmailNotifier(
        "http://notice.test/v1/events", "secret", opener=opener,
    )
    message = GmailMessage("provider-id", "42", "sender@example.test", "body", "Тема письма", "owner@example.test")
    notifier.notify(account="gmail", source="gmail", message=message)
    notifier.notify(account="gmail", source="gmail", message=message)

    first, second = calls
    first_payload = json.loads(first[0].data)
    second_payload = json.loads(second[0].data)
    assert first[1] == 10
    assert first_payload == second_payload
    assert first_payload["kind"] == "notification"
    assert first_payload["severity"] == "notice"
    assert first_payload["title"] == "Новое письмо: owner@example.test"
    assert "Почтовый аккаунт: owner@example.test" in first_payload["body"]
    assert "sender@example.test" in first_payload["body"]
    assert "Тема письма" in first_payload["body"]
    assert first[0].get_header("Idempotency-key") == second[0].get_header("Idempotency-key")
