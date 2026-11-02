"""Offline question-lease recovery against the actual UserIO store schema.

No network, production account, notification or real provider send.
"""
import sqlite3

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.question_coordination import (
    AccessDenied, Actor, Binding, Busy, QuestionCoordination, Rejected,
)
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class NoGeneration:
    def generate(self, *_args, **_kwargs):
        raise AssertionError("model must not run in offline lease test")


class NoSend:
    def send_reply(self, **_kwargs):
        raise AssertionError("no provider send permitted")


def test_terminal_original_receipt_can_be_adopted_without_replay(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "native-userio.sqlite3")
    service = UserIOService(store, NoGeneration(), NoSend())
    conversation_id, accepted = service.receive(
        InboxMessage(
            "telegram", "native-message-1", "human-fixture",
            "Addressed question", 1.0, conversation_kind="direct",
            peer_id="fixture-owner", sender_is_bot=False,
        ),
        route_id="telegram", account_ref="telegram:synthetic",
    )
    assert accepted
    event_seq = int(store.workspace_events()["events"][0]["seq"])
    now = [1000.0]
    saved = {"result:original"}
    actors = {
        "manager": Actor(
            "fixture-tenant", store.default_user_id, "manager",
            "verified-manager", frozenset({"telegram:synthetic"}),
            frozenset({"manager"}),
        ),
        "dot": Actor(
            "fixture-tenant", store.default_user_id, "dot",
            "verified-dot", frozenset({"telegram:synthetic"}),
            frozenset({"manager"}), True,
        ),
    }
    with store._lock, store._connection:
        store._connection.execute("""
            CREATE TABLE fixture_original_journal (
                request_id TEXT PRIMARY KEY,
                question_id TEXT NOT NULL,
                state TEXT NOT NULL,
                receipt_ref TEXT
            )
        """)

    def authorized_actor(context):
        try:
            return actors[context]
        except KeyError as exc:
            raise AccessDenied("invalid_authenticated_fixture") from exc

    def verified_binding(actor, src):
        c = store.conversation(conversation_id, user_id=actor.user_id)
        assert c is not None
        if src["account_ref"] != c["account_ref"] or src["conversation_id"] != c["id"]:
            raise AccessDenied("binding_identity_mismatch")
        return Binding("manager", "saved:" + c["id"], frozenset({"manager", "dot"}))

    def journal_reserve(conn, src, req):
        conn.execute(
            "INSERT INTO fixture_original_journal VALUES (?,?,?,?)",
            (req, src["question_id"], "pending", None),
        )

    def journal_read(src, req):
        row = store._connection.execute(
            "SELECT state,receipt_ref FROM fixture_original_journal "
            "WHERE request_id=? AND question_id=?",
            (req, src["question_id"]),
        ).fetchone()
        return None if row is None else {"state": row[0], "receipt_ref": row[1]}

    coordination = QuestionCoordination(
        store._connection, store._lock,
        tenant="fixture-tenant", token_key=b"fixture-lease-key-32-bytes-or-longer",
        resolve_actor=authorized_actor, resolve_binding=verified_binding,
        journal_reserve=journal_reserve, journal_read=journal_read,
        result_exists=lambda _src, ref: ref in saved,
        delivery_authorized=lambda _actor, _src, _req: True,
        clock=lambda: now[0],
    )
    try:
        coordination.install()
        first = coordination.acquire("manager", event_seq, "0", "acquire-1")
        coordination.result_saved(
            "manager", event_seq, "0", first["lease_token"], first["epoch"],
            "result-1", "result:original",
        )
        coordination.reserve_delivery(
            "manager", event_seq, "0", first["lease_token"], first["epoch"], "outgoing-1",
        )
        # Before provider receipt appears, even an expired worker cannot replay.
        now[0] += 301
        with pytest.raises(Busy):
            coordination.acquire("dot", event_seq, "0", "unknown-reclaim")
        assert store._connection.execute(
            "SELECT COUNT(*) FROM fixture_original_journal"
        ).fetchone()[0] == 1

        with store._lock, store._connection:
            store._connection.execute(
                "UPDATE fixture_original_journal SET state='sent',receipt_ref='receipt:original' "
                "WHERE request_id='outgoing-1'"
            )
        reconciliation = coordination.reconcile_delivery(
            "manager", event_seq, "0", "reconcile-original",
        )
        assert reconciliation["delivery_state"] == "sent"
        # The original result and receipt survive a change in holder/epoch.
        replacement = coordination.acquire("dot", event_seq, "0", "completion-only")
        assert replacement["epoch"] == first["epoch"] + 1
        assert replacement["result_ref"] == "result:original"
        with pytest.raises((Rejected, AccessDenied)):
            coordination.complete(
                "manager", event_seq, "0", first["lease_token"], first["epoch"],
                "stale-complete",
            )
        with pytest.raises(Busy):
            coordination.reserve_delivery(
                "dot", event_seq, "0", replacement["lease_token"],
                replacement["epoch"], "duplicate-send",
            )
        result = coordination.complete(
            "dot", event_seq, "0", replacement["lease_token"],
            replacement["epoch"], "complete-original",
        )
        assert result["receipt_ref"] == "receipt:original"
        assert result["state"] == "completed"
        assert store._connection.execute(
            "SELECT COUNT(*) FROM fixture_original_journal"
        ).fetchone()[0] == 1
    finally:
        store.close()
