from universal_userio.contracts import InboxMessage
from universal_userio.adapters import StoredChannelAdapter
from universal_userio.search import initialize_search, save_profile
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


def _conversation(store, service, *, account, peer, sender):
    cid, _ = service.receive(InboxMessage(
        source="telegram", message_id=f"{account}|{peer}:1", sender=sender,
        body="fixture", received_at=1, direction="incoming", peer_id=peer,
        conversation_kind="direct",
    ), route_id="telegram", account_ref=account)
    return cid


def test_chat_title_uses_synced_telegram_profile_name(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "names.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:8810909089"
    store.register_account(account_id=account, provider="telegram", display_name="Secretary",
                           can_read=True, can_reply=True, credential_ref="fixture")
    cid = _conversation(store, service, account=account, peer="900000111", sender="900000111")
    initialize_search(store)
    save_profile(store, store.default_user_id, cid,
        {"first_name": "Alice", "last_name": "Example", "username": "alice_example"})

    row = store.conversations(source="telegram", account_ref=account)[0]
    assert row["display_name"] == "Alice Example"
    store.close()


def test_chat_title_falls_back_to_a_linked_telegram_accounts_saved_name(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "linked-account.sqlite3")
    service = UserIOService(store, object(), object())
    owner = "telegram:8810909089"
    linked_peer = "540308572"
    store.register_account(account_id=owner, provider="telegram", display_name="Secretary",
                           can_read=True, can_reply=True, credential_ref="fixture-owner")
    store.register_account(account_id=f"telegram:{linked_peer}", provider="telegram",
                           display_name="Nikita Rosанов", can_read=True, can_reply=True,
                           credential_ref="fixture-linked")
    _conversation(store, service, account=owner, peer=linked_peer, sender=linked_peer)

    row = store.conversations(source="telegram", account_ref=owner)[0]
    assert row["display_name"] == "Nikita Rosанов"
    store.close()


def test_explicit_contact_alias_still_wins_over_synced_profile(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "alias.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:8810909089"
    store.register_account(account_id=account, provider="telegram", display_name="Secretary",
                           can_read=True, can_reply=True, credential_ref="fixture")
    cid = _conversation(store, service, account=account, peer="900000111", sender="900000111")
    initialize_search(store)
    save_profile(store, store.default_user_id, cid, {"first_name": "Alice", "last_name": "Example"})
    with store._connection:
        store._connection.execute("INSERT INTO contact_names(user_id,source,sender,name,updated_at) VALUES(?,?,?,?,?)",
            (store.default_user_id, "telegram", "900000111", "My contact", 2))

    row = store.conversations(source="telegram", account_ref=account)[0]
    assert row["display_name"] == "My contact"
    store.close()


def test_saved_messages_get_a_friendly_title_and_channel_list_uses_it(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "saved-messages.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:8810909089"
    store.register_account(account_id=account, provider="telegram", display_name="Secretary",
                           can_read=True, can_reply=True, credential_ref="fixture")
    _conversation(store, service, account=account, peer="8810909089", sender="8810909089")

    class TelegramList(StoredChannelAdapter):
        channel = "telegram"

    rows = TelegramList(store, service, store.default_user_id).list()
    assert rows[0]["title"] == "Избранное"
    store.close()
