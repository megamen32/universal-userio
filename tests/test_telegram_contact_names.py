import json
import sqlite3
import threading
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from universal_userio.http_api import handler
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
        store._connection.execute("INSERT INTO contact_names(user_id,source,sender,name,updated_at,is_explicit) VALUES(?,?,?,?,?,1)",
            (store.default_user_id, "telegram", "900000111", "My contact", 2))

    row = store.conversations(source="telegram", account_ref=account)[0]
    assert row["display_name"] == "My contact"
    store.close()


def test_refreshed_telegram_profile_replaces_synced_old_contact_name(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "refreshed-name.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:8810909089"
    store.register_account(account_id=account, provider="telegram", display_name="Secretary",
                           can_read=True, can_reply=True, credential_ref="fixture")
    cid = _conversation(store, service, account=account, peer="900000111", sender="900000111")
    initialize_search(store)
    with store._connection:
        store._connection.execute("INSERT INTO contact_names(user_id,source,sender,name,updated_at,is_explicit) VALUES(?,?,?,?,?,0)",
            (store.default_user_id, "telegram", "900000111", "Old Telegram Name", 2))
    save_profile(store, store.default_user_id, cid, {"first_name": "Alice", "last_name": "Example"})

    row = store.conversations(source="telegram", account_ref=account)[0]
    assert row["display_name"] == "Alice Example"
    store.close()


def test_legacy_contact_names_migrate_without_sender_name_column(tmp_path):
    path = tmp_path / "legacy-contact-names.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE contact_names(user_id TEXT,source TEXT,sender TEXT,name TEXT,updated_at REAL,PRIMARY KEY(user_id,source,sender))")
    connection.execute("INSERT INTO contact_names VALUES(?,?,?,?,?)",
        ("user_owner", "telegram", "900000111", "Old Telegram Name", 2))
    connection.commit()
    connection.close()

    store = SQLiteUserIOStore(path)
    row = store._connection.execute("SELECT name,is_explicit FROM contact_names").fetchone()
    assert row["name"] == "Old Telegram Name"
    assert row["is_explicit"] == 0
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


def test_conversation_http_list_exposes_the_synced_name_to_the_web_client(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "http-names.sqlite3")
    service = UserIOService(store, object(), object())
    account = "telegram:8810909089"
    store.register_account(account_id=account, provider="telegram", display_name="Secretary",
                           can_read=True, can_reply=True, credential_ref="fixture")
    cid = _conversation(store, service, account=account, peer="900000111", sender="900000111")
    initialize_search(store)
    save_profile(store, store.default_user_id, cid, {"first_name": "Alice", "last_name": "Example"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="test-token"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/conversations?source=telegram&account_ref={account}",
            headers={"Authorization": "Bearer test-token"},
        )
        with urlopen(request) as response:
            rows = json.loads(response.read())["conversations"]
        assert rows[0]["id"] == cid
        assert rows[0]["display_name"] == "Alice Example"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        store.close()
