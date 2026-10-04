import json

import pytest

from universal_userio.channels.gmail import HimalayaReader
from universal_userio.gmail_ingress import (
    GmailMessage,
    NoticePlaceEmailNotifier,
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
    message = GmailMessage("provider-id", "42", "sender@example.test", "body", "Тема письма")
    notifier.notify(account="gmail", source="gmail", message=message)
    notifier.notify(account="gmail", source="gmail", message=message)

    first, second = calls
    first_payload = json.loads(first[0].data)
    second_payload = json.loads(second[0].data)
    assert first[1] == 10
    assert first_payload == second_payload
    assert first_payload["kind"] == "notification"
    assert first_payload["severity"] == "notice"
    assert first_payload["title"] == "Новое письмо в Gmail"
    assert "sender@example.test" in first_payload["body"]
    assert "Тема письма" in first_payload["body"]
    assert first[0].get_header("Idempotency-key") == second[0].get_header("Idempotency-key")
