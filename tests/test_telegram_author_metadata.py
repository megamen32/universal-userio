from __future__ import annotations

import json

from universal_userio.adapters import TelegramChannelAdapter
from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def suggest(self, **_kwargs):
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs):
        return "receipt"


def test_telegram_read_promotes_group_author_from_routing_metadata(tmp_path) -> None:
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    routing = {
        "version": 1,
        "group": True,
        "chat_id": "-5453051466",
        "group_name": "ИИ-Бенчмарки",
        "author_id": "16558149",
        "author_name": "Dmitry Dubovskoy",
        "addressed": True,
    }
    conversation_id, _ = service.receive(
        InboxMessage(
            "telegram", "-5453051466:1644", "ИИ-Бенчмарки", "Щит из хард", 1.0,
            attachments=({
                "kind": "telegram_routing",
                "content_type": "application/vnd.userio.telegram-routing+json",
                "filename": "telegram-routing.json",
                "provider_ref": json.dumps(routing),
            },),
        ),
        route_id="telegram",
    )

    result = TelegramChannelAdapter(store, service, store.default_user_id).read(
        chat_id=conversation_id,
    )
    message = result["chat"]["messages"][0]

    assert message["author"] == {"id": "16558149", "name": "Dmitry Dubovskoy"}
    assert message["addressed_to_owner"] is True
