from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from universal_userio.contracts import InboxMessage
from universal_userio.messenger import Messenger
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


ACCOUNT = "telegram:900000103"
PEER = "-10042"


class EditOutbox:
    def supports_edit(self, account_ref, peer_id):
        return True

    def __init__(self):
        self.edits = []

    def edit_message(self, *, chat_id, account_ref, message_id, expected_text, text):
        self.edits.append({"chat_id": chat_id, "account_ref": account_ref,
                           "message_id": message_id, "expected_text": expected_text, "text": text})
        return {"ok": True, "account_id": account_ref, "peer_id": chat_id,
                "message_id": message_id.rsplit(":", 1)[-1], "text": text,
                "edit_date": 20, "out": True}


def test_qr_telegram_capabilities_expose_edit_only_when_outbox_supports_it(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = EditOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    store.register_account(account_id=ACCOUNT, provider="telegram", display_name="Owner",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    conversation_id, _ = service.receive(
        InboxMessage("telegram", f"{PEER}:77", "900000103", "before", 10,
                     direction="outgoing", peer_id=PEER, conversation_kind="direct"),
        route_id="telegram", account_ref=ACCOUNT,
    )

    capabilities = Messenger(store, service, store.owner()).capabilities(ACCOUNT)

    assert capabilities["operations"]["edit"] == {"supported": True, "allowed": True, "reason": ""}
    assert store.conversation(conversation_id)["messages"][0]["direction"] == "outgoing"

    events_before = store._connection.execute("SELECT COUNT(*) FROM workspace_events").fetchone()[0]
    session = store.create_oauth_session(store.default_user_id)
    from universal_userio.http_api import handler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-fixture"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        view = Request(f"http://127.0.0.1:{server.server_port}/v1/conversations/{conversation_id}",
            headers={"Cookie":"userio_web_session="+session})
        with urlopen(view, timeout=3) as response:
            conversation_view = json.load(response)
        assert conversation_view["messages"][0]["can_edit"] is True
        request = Request(
            f"http://127.0.0.1:{server.server_port}/v1/conversations/{conversation_id}/messages/{ACCOUNT}|{PEER}:77",
            method="PATCH", data=json.dumps({"body":"after", "expected_body":"before"}).encode(),
            headers={"Cookie":"userio_web_session="+session, "Content-Type":"application/json"},
        )
        try:
            with urlopen(request, timeout=3) as response:
                payload = json.load(response)
                assert response.status == 200
        except HTTPError as error:
            raise AssertionError(error.read().decode()) from error
    finally:
        server.shutdown(); server.server_close(); thread.join()

    source_message_id = f"{ACCOUNT}|{PEER}:77"
    assert payload["message"]["body"] == "after"
    assert outbox.edits == [{"chat_id": PEER, "account_ref": ACCOUNT,
                             "message_id": source_message_id, "expected_text": "before", "text": "after"}]
    assert store.message(source="telegram", message_id=source_message_id)["body"] == "after"
    assert store._connection.execute("SELECT COUNT(*) FROM workspace_events").fetchone()[0] == events_before
    store.close()


def test_messenger_propose_respects_conversation_scoped_edit_guard(tmp_path):
    class GuardedOutbox(EditOutbox):
        def supports_edit(self, account_ref, peer_id):
            return peer_id != PEER

    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = GuardedOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    store.register_account(account_id=ACCOUNT, provider="telegram", display_name="Owner",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    conversation_id, _ = service.receive(
        InboxMessage("telegram", f"{PEER}:77", "900000103", "before", 10,
                     direction="outgoing", peer_id=PEER, conversation_kind="direct"),
        route_id="telegram", account_ref=ACCOUNT,
    )
    session = store.create_oauth_session(store.default_user_id)
    from universal_userio.http_api import handler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service, token="owner-fixture"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/v1/conversations/{conversation_id}",
            headers={"Cookie":"userio_web_session="+session})
        with urlopen(request, timeout=3) as response:
            conversation_view = json.load(response)
        assert conversation_view["messages"][0]["can_edit"] is False
    finally:
        server.shutdown(); server.server_close(); thread.join()

    try:
        Messenger(store, service, store.owner()).propose(ACCOUNT, conversation_id,
            f"{ACCOUNT}|{PEER}:77", "edit", {"text":"after","expected_text":"before"},
            "telegram-edit-guard-1234")
    except PermissionError as error:
        assert str(error) == "provider_not_supported_for_conversation"
    else:
        raise AssertionError("a guarded relay pair must not create an edit action")
    assert outbox.edits == []
    store.close()


def test_telegram_edit_approval_rejects_stale_message_before_provider_call(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = EditOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    store.register_account(account_id=ACCOUNT, provider="telegram", display_name="Owner",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    conversation_id, _ = service.receive(
        InboxMessage("telegram", f"{PEER}:77", "900000103", "before", 10,
                     direction="outgoing", peer_id=PEER, conversation_kind="direct"),
        route_id="telegram", account_ref=ACCOUNT,
    )
    api = Messenger(store, service, store.owner())
    action = api.propose(ACCOUNT, conversation_id, f"{ACCOUNT}|{PEER}:77", "edit",
                         {"text": "after", "expected_text": "before"},
                         "telegram-edit-stale-1234")
    service.receive(
        InboxMessage("telegram", f"{PEER}:77", "900000103", "changed elsewhere", 10,
                     direction="outgoing", peer_id=PEER, conversation_kind="direct",
                     edited_at=15, reconciliation=True),
        route_id="telegram", account_ref=ACCOUNT,
    )

    try:
        api.approve(action["id"], action["hash"], True)
    except ValueError as error:
        assert "source_snapshot_conflict" in str(error)
    else:
        raise AssertionError("stale edit must be rejected before provider mutation")
    assert outbox.edits == []
    assert api.get(action["id"])["status"] == "proposed"
    store.close()


def test_telegram_web_edit_rejects_provider_length_limit_before_approval(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    outbox = EditOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    store.register_account(account_id=ACCOUNT, provider="telegram", display_name="Owner",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:account-1")
    conversation_id, _ = service.receive(
        InboxMessage("telegram", f"{PEER}:77", "900000103", "before", 10,
                     direction="outgoing", peer_id=PEER, conversation_kind="direct"),
        route_id="telegram", account_ref=ACCOUNT,
    )
    try:
        service.edit_message(conversation_id, message_id=f"{ACCOUNT}|{PEER}:77",
                             body="🙂" * 2049, expected_body="before")
    except ValueError as error:
        assert "4096" in str(error)
    else:
        raise AssertionError("Telegram UTF-16 message limit must be checked before approval")
    assert outbox.edits == []
    store.close()
