from __future__ import annotations

from universal_userio.adapters import UnifiedChannels
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs: object) -> str:
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs: object) -> str:
        return "unexpected"


class FakeMaxClient:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, *, chat_id: str, text: str) -> dict:
        self.sent.append((str(chat_id), text))
        return {"chat_id": str(chat_id), "message_id": "m-42", "receipt": "max-ws-accepted"}


def _service(tmp_path, *, manual: bool = False):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite")
    client = FakeMaxClient()
    service = UserIOService(
        store, Generator(), Outbox(), max_client=client,
        max_user_id=store.default_user_id, max_route_id="max",
        max_manual_approve=manual,
    )
    return store, client, service


def test_max_message_syncs_to_userio_and_approved_draft_sends_via_client(tmp_path) -> None:
    store, client, service = _service(tmp_path)
    channels = UnifiedChannels(store, service, store.default_user_id)

    message = InboxMessage("max", "max-9001-111", "777", "Need help", 1_781_700_000.0)
    _, accepted, draft = service.receive_and_plan(message, route_id="max")
    assert accepted is True and draft is not None

    chats = channels.adapter("max").list()
    assert len(chats) == 1
    assert chats[0]["channel"] == "max"
    assert chats[0]["last_message_snippet"] == "Need help"

    result = channels.adapter("max").send(chat_id=chats[0]["id"], text="We can help.")
    approved = service.approve(result.id, user_id=store.default_user_id)
    assert approved.status == "approved"
    assert client.sent == [("777", "We can help.")]
    assert approved.receipt == "m-42"


def test_max_manual_approve_lock_blocks_auto_send(tmp_path) -> None:
    store, client, service = _service(tmp_path, manual=True)
    store.register_identity(
        source="max", external_id="777", identity_id="person_max", display_name="MAX Peer"
    )
    store.set_rule(identity_id="person_max", source="max", route_id="max", mode="auto_send")

    message = InboxMessage("max", "max-9001-222", "777", "Auto please", 1_781_700_100.0)
    _, accepted, draft = service.receive_and_plan(message, route_id="max")

    assert accepted is True
    assert draft is not None and draft.status == "proposed"
    assert client.sent == []

    approved = service.approve(draft.id, user_id=store.default_user_id)
    assert approved.status == "approved"
    assert client.sent == [("777", draft.body)]
