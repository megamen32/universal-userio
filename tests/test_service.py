from __future__ import annotations

from universal_userio.contracts import InboxMessage
from universal_userio.adapters import NoticePlaceOutboxClient, NoticePlaceRoute, inbox_message_from_envelope
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, *, conversation_id: str, latest_message: InboxMessage) -> str:
        return f"Thanks for: {latest_message.body}"


class Outbox:
    def __init__(self) -> None:
        self.calls = []

    def send_reply(self, *, route_id: str, conversation_id: str, draft_id: str, body: str) -> str:
        self.calls.append((route_id, conversation_id, draft_id, body))
        return "delivery-receipt-1"


def test_message_to_draft_to_approved_outbox_reply(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    message = InboxMessage("telegram", "chat:1", "chat", "hello", 1.0)

    conversation_id, accepted = service.receive(message, route_id="telegram-reply")
    draft = service.propose(conversation_id, message)
    approved = service.approve(draft.id)

    assert accepted is True
    assert approved.status == "approved"
    assert outbox.calls == [("telegram-reply", conversation_id, draft.id, "Thanks for: hello")]
    assert store.conversation(conversation_id)["drafts"][0]["outbox_receipt"] == "delivery-receipt-1"


def test_duplicate_ingress_is_suppressed_and_rejection_never_sends(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = Outbox()
    service = UserIOService(store, Generator(), outbox)
    message = InboxMessage("vk", "42", "customer", "question", 1.0)

    conversation_id, accepted = service.receive(message, route_id="vk-reply")
    _, duplicate = service.receive(message, route_id="vk-reply")
    draft = service.propose(conversation_id, message)
    rejected = store.reject(draft.id)

    assert accepted is True
    assert duplicate is False
    assert rejected.status == "rejected"
    assert outbox.calls == []
    try:
        service.approve(draft.id)
    except ValueError as error:
        assert str(error) == "draft is not approvable"
    else:
        raise AssertionError("rejected draft was sent")
    assert outbox.calls == []


def test_canonical_inbox_envelope_and_policy_bound_outbox_contract() -> None:
    message = inbox_message_from_envelope(
        {"schema": "universal.inbox.message.v1", "source": "vk", "message_id": "m-1", "sender": "customer", "body": "hello"},
        received_at=1.0,
    )
    requests = []

    class Response:
        status = 202

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read() -> bytes:
            return b'{"event_id":"evt_1"}'

    def runner(request, *, timeout):
        requests.append((request, timeout))
        return Response()

    outbox = NoticePlaceOutboxClient({"vk-reply": NoticePlaceRoute("http://127.0.0.1:8091", "scoped-token", "userio")}, runner=runner)
    receipt = outbox.send_reply(route_id="vk-reply", conversation_id="conv_1", draft_id="draft_1", body="answer")

    assert message.conversation_key == "vk:customer"
    assert receipt == "evt_1"
    payload = __import__("json").loads(requests[0][0].data)
    assert payload["event_type"] == "userio.reply.v1"
    assert "target" not in payload
    assert requests[0][0].get_header("Authorization") == "Bearer scoped-token"
