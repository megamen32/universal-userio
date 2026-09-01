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


class Gateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def inbound(self) -> list[InboxMessage]:
        return [InboxMessage("sms", "sms-1", "+15551234567", "Need help", 1_700_000_000.0)]

    def send(self, *, to: str, body: str) -> str:
        self.sent.append((to, body))
        return "android-accepted-1"


def test_android_sms_syncs_to_userio_and_approved_draft_uses_gateway(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite")
    gateway = Gateway()
    service = UserIOService(
        store, Generator(), Outbox(), sms_gateway=gateway,
        sms_user_id=store.default_user_id, sms_route_id="sms",
    )
    channels = UnifiedChannels(store, service, store.default_user_id)

    chats = channels.adapter("sms").list()
    assert len(chats) == 1
    assert chats[0]["channel"] == "sms"
    assert chats[0]["last_message_snippet"] == "Need help"

    draft = channels.adapter("sms").send(chat_id=chats[0]["id"], text="We can help.")
    approved = service.approve(draft.id, user_id=store.default_user_id)
    assert approved.status == "approved"
    assert gateway.sent == [("+15551234567", "We can help.")]
