"""Native polling persists messages/cursors without a browser process."""
from universal_userio import dion_chat
from universal_userio.adapters import UnifiedChannels
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def test_poll_restart_reuses_cursor_and_deduplicates_history(tmp_path, monkeypatch):
    account_id = "dion:org:user"
    database = tmp_path / "db.sqlite3"
    cursors = []

    class Client:
        def list_conversations(self, **kwargs):
            return {"conversations": [{"conversation_id": "room", "name": "Fixture",
                                        "conversation_type": "DM"}]}
        def history(self, conversation_id, *, after, limit):
            cursors.append(after)
            return {"messages": [{
                "message_id": "m1", "conversation_id": "room", "creator_id": "peer",
                "created_at": "2026-11-26T12:00:00.000Z", "content": "fixture",
            }]}

    monkeypatch.setattr(dion_chat, "_client", lambda *args: (Client(), {"provider_user_id": "user"}))
    for index in range(2):
        store = SQLiteUserIOStore(database)
        try:
            service = UserIOService(store, object(), object())
            if index == 0:
                store.register_account(account_id=account_id, provider="dion", display_name="Fixture",
                                       can_read=True, can_reply=True, credential_ref="fixture",
                                       enabled=True, user_id=store.default_user_id)
                store.bind_channel_route(user_id=store.default_user_id, source="dion", route_id="dion")
            dion_chat.poll_dion(service, {"user_id": store.default_user_id, "account_id": account_id})
            adapter = UnifiedChannels(store, service, store.default_user_id).adapter("dion")
            chats = adapter.list(limit=10)
            assert len(chats) == 1
            messages = adapter.read(chat_id=chats[0]["id"])["chat"]["messages"]
            assert len(messages) == 1
            assert messages[0]["body"] == "fixture"
        finally:
            store.close()
    assert cursors == ["", "2026-11-26T11:59:59.999Z"]
