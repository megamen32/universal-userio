"""Offline acceptance against the ACTUAL Universal UserIO SQLite store.

No browser, provider, Telegram sends, credentials or external network.
This is intentionally separate from tests/test_question_coordination.py.
"""
import json
import sqlite3

import pytest

from universal_userio.question_coordination import Actor, AccessDenied, Busy, Rejected
from universal_userio.question_coordination_adapter import bind_question_coordination
from universal_userio.store import SQLiteUserIOStore

ACCOUNT = "telegram:fixture"
REQUEST = "manager_request_0123456789abcdef"


@pytest.fixture
def integration(tmp_path):
    path = tmp_path / "native-userio.db"
    store = SQLiteUserIOStore(path)
    user = store.default_user_id
    with store._lock, store._connection:
        store._connection.execute("""
            INSERT INTO conversations
            (user_id,id,conversation_key,route_id,source,sender,
             updated_at,account_ref,peer_id,conversation_kind)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (user, "conversation1", "native:test:conversation1",
              "telegram", "telegram", "human-fixture", 1.0, ACCOUNT, "42", "direct"))
        store._connection.execute("""
            INSERT INTO messages
            (user_id,source,message_id,conversation_id,sender,body,
             received_at,direction,sender_is_bot)
            VALUES (?,?,?,?,?,?,?,'incoming',0)
        """, (user, "telegram", "telegram:fixture|42:42", "conversation1",
              "human-fixture", "question in fixture", 1.0))
        cursor = store._connection.execute("""
            INSERT INTO workspace_events (user_id,source,message_id,conversation_id,eligible)
            VALUES (?,?,?,?,1)
        """, (user, "telegram", "telegram:fixture|42:42", "conversation1"))
        seq = cursor.lastrowid
        store._connection.execute("""
            CREATE TABLE messenger_actions (
              user_id TEXT NOT NULL,id TEXT NOT NULL,request_id TEXT NOT NULL,
              envelope TEXT NOT NULL,hash TEXT NOT NULL,status TEXT NOT NULL,
              created_at REAL NOT NULL,receipt TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(user_id,id),UNIQUE(user_id,request_id))
        """)
        envelope = {
            "user_id": user, "account_ref": ACCOUNT,
            "conversation_id": "conversation1", "source_message_id": "42",
            "action": "reply", "request_id": REQUEST,
        }
        store._connection.execute("""
            INSERT INTO messenger_actions
            (user_id,id,request_id,envelope,hash,status,created_at)
            VALUES (?,?,?,?,?,'proposed',?)
        """, (user, "action-fixture", REQUEST, json.dumps(envelope), "snapshot-fixture", 1.0))
    actor = {
        "manager": Actor("tenant-fixture", user, "manager", "signed-manager",
                         frozenset({ACCOUNT}), frozenset({"manager"})),
        "dot": Actor("tenant-fixture", user, "dot", "signed-dot",
                     frozenset({ACCOUNT}), frozenset({"manager"}), True),
    }
    live = {"approved": True, "send": True, "holders": frozenset({"manager", "dot"})}

    def verified_actor(context):
        if context not in actor:
            raise AccessDenied("unverified_caller")
        return actor[context]

    engine = bind_question_coordination(
        store, tenant="tenant-fixture",
        token_key=b"fixture-only-key-that-is-never-used-in-prod",
        verified_actor=verified_actor,
        permitted_holders=lambda who, source: live["holders"],
        human_approval_and_send_grant=lambda who, source, request: (
            live["approved"] and live["send"]
            and request == "messenger:" + REQUEST
        ),
    )
    engine.install()
    try:
        yield store, engine, seq, live, path
    finally:
        store.close()


def test_native_original_journal_receipt_with_no_replay(integration):
    store, engine, seq, live, path = integration
    first = engine.acquire("manager", seq, "0", "acquire-native")
    assert first["epoch"] == 1
    engine.result_saved("manager", seq, "0", first["lease_token"], 1,
                        "result-native", "messenger:" + REQUEST)
    engine.reserve_delivery("manager", seq, "0", first["lease_token"], 1,
                            "messenger:" + REQUEST)
    calls = []

    def existing_send(src, original_id):
        assert original_id == "messenger:" + REQUEST
        calls.append(original_id)
        independent = sqlite3.connect(path)
        try:
            with independent:
                independent.execute("""
                    UPDATE messenger_actions SET status='accepted',receipt=?
                    WHERE user_id=? AND request_id=?
                """, ('{"receipt":"fixture-provider-receipt"}',
                      store.default_user_id, REQUEST))
        finally:
            independent.close()

    sent = engine.send_fenced("manager", seq, "0", first["lease_token"], 1,
                             "dispatch-native", existing_send)
    assert sent["delivery_state"] == "sent"
    assert sent["receipt_ref"] == "messenger-action:action-fixture"
    repeated = engine.send_fenced("manager", seq, "0", first["lease_token"], 1,
                                  "dispatch-native", existing_send)
    assert repeated["idempotent"] is True
    assert calls == ["messenger:" + REQUEST]
    done = engine.complete("manager", seq, "0", first["lease_token"], 1, "done-native")
    assert done["state"] == "completed"


def test_native_caller_and_current_approval_denied(integration):
    store, engine, seq, live, path = integration
    with pytest.raises(AccessDenied):
        engine.acquire("forged-worker", seq, "0", "forged")
    claim = engine.acquire("manager", seq, "0", "acquire")
    engine.result_saved("manager", seq, "0", claim["lease_token"], 1,
                        "result", "messenger:" + REQUEST)
    live["approved"] = False
    with pytest.raises(AccessDenied):
        engine.reserve_delivery("manager", seq, "0", claim["lease_token"], 1,
                                "messenger:" + REQUEST)
    assert store._connection.execute(
        "SELECT delivery_request_id FROM question_coordination"
    ).fetchone()[0] is None


def test_native_unknown_blocks_handoff_replay(integration):
    store, engine, seq, live, path = integration
    claim = engine.acquire("manager", seq, "0", "acquire")
    engine.result_saved("manager", seq, "0", claim["lease_token"], 1,
                        "result", "messenger:" + REQUEST)
    engine.reserve_delivery("manager", seq, "0", claim["lease_token"], 1,
                            "messenger:" + REQUEST)
    output = engine.send_fenced("manager", seq, "0", claim["lease_token"], 1,
                                "dispatch", lambda src, req: None)
    assert output["delivery_state"] == "unknown"
    hold = engine.handoff("dot", seq, "0", "handoff", "dot")
    assert hold["state"] == "transferring"
    with pytest.raises(Busy):
        engine.acquire("dot", seq, "0", "do-not-replay")
    assert store._connection.execute(
        "SELECT COUNT(*) FROM messenger_actions WHERE status='proposed'"
    ).fetchone()[0] == 1


def test_native_source_rebinding_refused(integration):
    store, engine, seq, live, path = integration
    claim = engine.acquire("manager", seq, "0", "acquire")
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE conversations SET account_ref='telegram:other' WHERE id='conversation1'"
        )
    with pytest.raises(AccessDenied):
        engine.pause("manager", seq, "0", "pause-after-rebind")



def test_provider_entrypoint_fence_blocks_plain_approve_and_accepts_internal_permit(integration):
    from universal_userio.messenger import Messenger

    store, engine, seq, live, path = integration
    current = store._connection.execute(
        "SELECT envelope FROM messenger_actions WHERE request_id=?", (REQUEST,)
    ).fetchone()
    action = {"envelope": json.loads(current["envelope"])}
    messenger = Messenger(store, None, store.owner())
    # A normal, unrelated manual action does not require question coordination.
    messenger._enforce_question_action(action)
    claim = engine.acquire("manager", seq, "0", "new-acquire")
    engine.result_saved("manager", seq, "0", claim["lease_token"], 1,
                        "new-result", "messenger:" + REQUEST)
    engine.reserve_delivery("manager", seq, "0", claim["lease_token"], 1,
                            "messenger:" + REQUEST)
    # The raw approve path cannot cross a question claim without a server permit.
    with pytest.raises(PermissionError, match="question_delivery_fence_conflict"):
        messenger._enforce_question_action(action)

    called = []
    def native_transport(src, req):
        with pytest.raises(PermissionError, match="question_delivery_fence_required"):
            messenger._enforce_question_action(action)
        messenger._enforce_question_action(
            action, lambda row, old: row["question_id"] == src["question_id"]
            and row["holder"] == "manager" and row["epoch"] == 1,
        )
        called.append(req)
        # No provider is ever invoked; this is a durable UNKNOWN test fixture.

    result = engine.send_fenced("manager", seq, "0", claim["lease_token"], 1,
                                "dispatch-provider-guard", native_transport)
    assert result["delivery_state"] == "unknown"
    assert called == ["messenger:" + REQUEST]



def test_draft_and_triage_actual_send_boundary_fails_closed(integration):
    import hashlib
    from universal_userio.service import UserIOService

    store, engine, seq, live, path = integration
    user = store.default_user_id
    class NeverGenerate:
        def generate(self, *args, **kwargs):
            raise AssertionError("no generation authorized")
    class NeverSend:
        def send_reply(self, **kwargs):
            raise AssertionError("no provider send authorized")

    service = UserIOService(store, NeverGenerate(), NeverSend())
    claim = engine.acquire("manager", seq, "0", "draft-claim")
    with store._lock, store._connection:
        store._connection.execute("""
            INSERT INTO drafts (user_id,id,conversation_id,body,status,created_at)
            VALUES (?,?,?,?,'proposed',?)
        """, (user, "draft-fixture", "conversation1", "saved reply", 1.0))
        store._connection.execute("""
            INSERT INTO draft_reply_origins
            (user_id,draft_id,conversation_id,account_ref,peer_id,
             message_key,provider_message_id,source_body_sha256)
            VALUES (?,?,?,?,?,?,?,?)
        """, (user, "draft-fixture", "conversation1", ACCOUNT, "42",
              "telegram:fixture|42:42", "42",
              hashlib.sha256(b"question in fixture").hexdigest()))
        store._connection.execute("""
            UPDATE question_coordination SET delivery_request_id=?,
                delivery_state='unknown' WHERE question_id=?
        """, ("draft:draft-fixture", claim["question_id"]))
    with pytest.raises(PermissionError, match="question_delivery_fence_required"):
        service.approve("draft-fixture", user_id=user)
    assert store.draft("draft-fixture", user_id=user).status == "proposed"
    service._enforce_question_draft_fence(
        "draft-fixture", user,
        lambda row, draft_id: row["question_id"] == claim["question_id"]
        and row["epoch"] == claim["epoch"] and draft_id == "draft-fixture",
    )
    # An unrelated prepared draft is not automatically blocked.
    service._enforce_question_draft_fence("not-associated-with-question", user)
