import json
import time

from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def generate(self, *_args, **_kwargs):
        return "draft"


class Outbox:
    def send_reply(self, **_kwargs):
        return "receipt"


def test_conversation_read_expires_only_old_proposed_drafts(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "source", "alice", "question", 100.0),
        route_id="telegram",
    )
    expired = service.create_manual_draft(conversation_id, body="expire me")
    sending = service.create_manual_draft(conversation_id, body="outcome unknown")
    store.claim_draft_send(sending.id, user_id=store.default_user_id)
    approved = service.create_manual_draft(conversation_id, body="sent already")
    store.approve(approved.id, "durable-receipt", user_id=store.default_user_id)
    now = time.time()
    with store._connection:
        store._connection.execute(
            "UPDATE drafts SET created_at=? WHERE id IN (?,?,?)",
            (now - 3 * 86400 - 1, expired.id, sending.id, approved.id),
        )

    conversation = store.conversation(conversation_id, user_id=store.default_user_id)

    assert {draft["id"] for draft in conversation["drafts"]} == {sending.id, approved.id}
    assert store.draft(sending.id, user_id=store.default_user_id).status == "sending"
    sent = store.draft(approved.id, user_id=store.default_user_id)
    assert sent.status == "approved"
    assert sent.receipt == "durable-receipt"


def test_expiration_preserves_triage_draft_with_uncertain_delivery(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "userio.sqlite3")
    service = UserIOService(store, Generator(), Outbox())
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "source", "alice", "question", 100.0),
        route_id="telegram",
    )
    draft = service.create_manual_draft(conversation_id, body="may already be sent")
    now = time.time()
    user = store.default_user_id
    with store._connection:
        store._connection.execute(
            "UPDATE drafts SET created_at=? WHERE user_id=? AND id=?",
            (now - 3 * 86400 - 1, user, draft.id),
        )
        store._connection.execute(
            """INSERT INTO workspace_triage
               (user_id,event_seq,request_id,status,result_json,draft_ids_json,send_state,
                selected_draft_id,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (user, 1, "triage-1", "completed", json.dumps({"decision": "notify"}),
             json.dumps([draft.id]), "sending", draft.id, now, now),
        )

    assert store.expire_proposed_drafts(now=now, user_id=user) == 0
    assert store.draft(draft.id, user_id=user).status == "proposed"
    triage = store.workspace_triage(event_seq=1, request_id="triage-1", user_id=user)
    assert triage["send_state"] == "sending"
    assert [item["id"] for item in triage["drafts"]] == [draft.id]
