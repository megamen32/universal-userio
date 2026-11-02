"""Offline verified-principal and native journal adapter tests, without sends."""
import json
from pathlib import Path

import pytest

from universal_userio.contracts import InboxMessage, UserPrincipal
from universal_userio.question_coordination import AccessDenied, Busy, Rejected
from universal_userio.question_runtime import QuestionRuntime
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class NoGenerator:
    def generate(self, *_args, **_kwargs):
        raise AssertionError("no generation in journal test")


class NoOutbox:
    def send_reply(self, **_kwargs):
        raise AssertionError("no network send permitted")


@pytest.fixture()
def context(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "native-store.sqlite3")
    store.register_account(
        account_id="telegram:123456789", provider="telegram",
        display_name="Synthetic Dot", can_read=True, can_reply=True,
        credential_ref="telegram-qr:synthetic",
    )
    service = UserIOService(store, NoGenerator(), NoOutbox())
    cid, accepted = service.receive(
        InboxMessage(
            "telegram", "fixture-provider-id", "owner-fixture",
            "Human question", 1.0, conversation_kind="direct",
            peer_id="12345", sender_is_bot=False,
        ), route_id="telegram", account_ref="telegram:123456789",
    )
    assert accepted is True
    event = store.workspace_events()["events"][0]
    seq = int(event["seq"])
    persisted_source_id = str(event["message_id"])
    binding = tmp_path / "dot-binding.json"
    binding.write_text(json.dumps({
        "enabled": True, "owner": "12345",
        "room": "verified-room-1", "botAccountId": "telegram:123456789",
    }))
    binding.chmod(0o600)
    runtime = QuestionRuntime(
        store, key=b"test-existing-userio-lease-key-longer-32bytes",
        dot_binding_path=binding,
    )
    owner = store.owner()
    dot = UserPrincipal(owner.user_id, owner.username, owner.role, service_account=True)
    manager = UserPrincipal(
        owner.user_id, owner.username, owner.role,
        messenger_accounts=("telegram:123456789",),
    )
    with store._lock, store._connection:
        store._connection.execute("""
            CREATE TABLE messenger_actions (
                user_id TEXT NOT NULL, id TEXT NOT NULL,
                request_id TEXT NOT NULL, envelope TEXT NOT NULL,
                hash TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL,
                receipt TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(user_id,id), UNIQUE(user_id,request_id)
            )
        """)
        env = {
            "user_id": owner.user_id, "account_ref": "telegram:123456789",
            "conversation_id": cid, "source_message_id": persisted_source_id,
            "source_hash": "verified-immutable-source", "action": "reply",
            "action_id": "action_fixture", "request_id": "outgoing-fixture-id-123456",
            "payload": {"text": "Synthetic result"},
        }
        store._connection.execute(
            "INSERT INTO messenger_actions VALUES (?,?,?,?,?,'proposed',1.0,'')",
            (owner.user_id, "action_fixture", "outgoing-fixture-id-123456",
             json.dumps(env), "snapshot-hash"),
        )
    yield runtime, store, seq, dot, manager, cid, binding
    store.close()


def test_verified_actor_scope_and_message_level_handoff(context):
    runtime, store, seq, dot, manager, cid, binding = context
    first = runtime.dispatch(dot, "acquire", {
        "event_seq": seq, "revision": "0", "request_id": "dot-acquire-1",
    })
    assert first["holder"] == "dot"
    with pytest.raises(Busy):
        runtime.dispatch(manager, "acquire", {
            "event_seq": seq, "revision": "0", "request_id": "mgr-loses-1",
        })
    assert runtime.dispatch(dot, "pause", {
        "event_seq": seq, "revision": "0", "request_id": "pause-one-1",
    })["state"] == "paused"
    assert runtime.dispatch(dot, "handoff", {
        "event_seq": seq, "revision": "0", "request_id": "handoff-mgr-1",
        "target_holder": "manager",
    })["holder"] == "manager"
    resumed = runtime.dispatch(manager, "resume", {
        "event_seq": seq, "revision": "0", "request_id": "resume-one-1",
    })
    assert resumed["holder"] == "manager"
    with pytest.raises((Rejected, AccessDenied)):
        runtime.dispatch(dot, "renew", {
            "event_seq": seq, "revision": "0", "request_id": "old-renew-1",
            "epoch": first["epoch"], "lease_token": first["lease_token"],
        })
    assert runtime.dispatch(manager, "audit", {"event_seq": seq})[-1]["action"] == "rejected"


def test_saved_messenger_action_is_only_original_outgoing_journal(context):
    runtime, store, seq, dot, manager, cid, binding = context
    lease = runtime.dispatch(manager, "acquire", {
        "event_seq": seq, "revision": "0", "request_id": "manager-acquire-1",
    })
    result = runtime.dispatch(manager, "result_saved", {
        "event_seq": seq, "revision": "0",
        "epoch": lease["epoch"], "lease_token": lease["lease_token"],
        "request_id": "result-stored-1",
        "result_ref": "messenger:action_fixture",
    })
    assert result["result_ref"] == "messenger:action_fixture"
    # Only trusted adapter, not public RPC, can reserve outgoing.
    with pytest.raises(Rejected):
        runtime.dispatch(manager, "reserve_delivery", {"event_seq": seq})
    runtime.coordinator.reserve_delivery(
        manager, seq, "0", lease["lease_token"], lease["epoch"],
        "outgoing-fixture-id-123456",
    )
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE messenger_actions SET status='sending' WHERE id='action_fixture'",
        )
    assert runtime.dispatch(manager, "reconcile", {
        "event_seq": seq, "revision": "0", "request_id": "receipt-unknown-1",
    })["delivery_state"] == "unknown"
    with pytest.raises(Busy):
        runtime.dispatch(dot, "acquire", {
            "event_seq": seq, "revision": "0", "request_id": "not-replay-1",
        })
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE messenger_actions SET status='accepted',receipt=? WHERE id='action_fixture'",
            ('{"receipt":"synthetic-only"}',),
        )
    terminal = runtime.dispatch(manager, "reconcile", {
        "event_seq": seq, "revision": "0", "request_id": "receipt-terminal-1",
    })
    assert terminal["delivery_state"] == "sent"
    assert terminal["receipt_ref"] == "messenger:action_fixture"
    done = runtime.dispatch(manager, "complete", {
        "event_seq": seq, "revision": "0", "request_id": "done-1",
        "lease_token": lease["lease_token"], "epoch": lease["epoch"],
    })
    assert done["state"] == "completed"
    assert store._connection.execute(
        "SELECT COUNT(*) FROM messenger_actions"
    ).fetchone()[0] == 1


def test_rejected_foreign_principal_and_unverified_dot_binding(context):
    runtime, store, seq, dot, manager, cid, binding = context
    with pytest.raises(AccessDenied):
        runtime.dispatch(
            UserPrincipal(manager.user_id, "outsider", "user"),
            "acquire", {"event_seq": seq, "revision": "0", "request_id": "outsider-1"},
        )
    with pytest.raises(AccessDenied):
        runtime.dispatch(
            UserPrincipal(manager.user_id, "manager", "owner",
                          messenger_accounts=("telegram:other",)),
            "acquire", {"event_seq": seq, "revision": "0", "request_id": "wrong-acct-1"},
        )
    binding.chmod(0o644)
    with pytest.raises(AccessDenied):
        runtime.dispatch(dot, "acquire", {
            "event_seq": seq, "revision": "0", "request_id": "dot-revoked-1",
        })
    assert store._connection.execute(
        "SELECT COUNT(*) FROM question_coordination"
    ).fetchone()[0] == 0

def test_mcp_tools_preserve_current_messenger_and_read_only_permissions(context):
    from universal_userio.mcp_surface import UserIOMcpSurface
    from universal_userio.mcp_dispatch import UserIOToolDispatcher
    runtime, store, seq, dot, manager, cid, binding = context
    service = UserIOService(store, NoGenerator(), NoOutbox())
    service.question_runtime = runtime
    surface = UserIOMcpSurface(store, service)
    allowed = {t["name"] for t in surface.tool_manifest(principal=manager)["tools"]}
    assert "userio.messenger.question.acquire" in allowed
    readonly = UserPrincipal(manager.user_id, manager.username, manager.role, read_only=True)
    hidden = {t["name"] for t in surface.tool_manifest(principal=readonly)["tools"]}
    assert "userio.messenger.question.acquire" not in hidden
    args = {"event_seq": seq, "revision": "0", "request_id": "mcp-acquire-001"}
    assert surface.dispatch(
        "userio.messenger.question.acquire", args, principal=readonly
    ) == {"ok": False, "error": "question_scope_denied"}
    result = UserIOToolDispatcher(store, service).dispatch(
        "userio.messenger.question.acquire", args, principal=manager,
    )
    assert result["ok"] is True
    assert result["question"]["holder"] == "manager"
    assert surface.dispatch(
        "userio.messenger.question.audit", {"event_seq": seq}, principal=manager,
    )["question"][0]["action"] == "acquire"


def test_existing_http_service_auth_can_claim_only_bound_dot_source(context):
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from universal_userio.http_api import handler

    runtime, store, seq, dot, manager, cid, binding = context
    service = UserIOService(store, NoGenerator(), NoOutbox())
    owner_token = "fixture-service-token-is-not-real"
    server = ThreadingHTTPServer(("127.0.0.1", 0),
                                 handler(service, token=owner_token))
    service.question_runtime.dot_binding_path = binding
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def post(headers, operation, args):
            request = Request(
                "http://127.0.0.1:" + str(server.server_port) + "/v1/questions/operation",
                data=json.dumps({"operation": operation, "arguments": args}).encode(),
                headers={**headers, "Content-Type": "application/json"}, method="POST",
            )
            try:
                with urlopen(request, timeout=4) as resp:
                    return resp.status, json.loads(resp.read())
            except HTTPError as resp:
                return resp.code, json.loads(resp.read())
        args = {"event_seq": seq, "revision": "0", "request_id": "dot-http-acquire"}
        unauthenticated = post({}, "acquire", args)
        assert unauthenticated[0] == 403
        authorized = post({"Authorization": "Bearer " + owner_token}, "acquire", args)
        assert authorized[0] == 200
        assert authorized[1]["question"]["holder"] == "dot"
        assert post({"Authorization": "Bearer " + owner_token},
                    "audit", {"event_seq": seq})[1]["question"][0]["action"] == "acquire"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_provider_send_is_fenced_against_the_original_action(context):
    runtime, store, seq, dot, manager, cid, binding = context
    lease = runtime.dispatch(manager, "acquire", {
        "event_seq": seq, "revision": "0", "request_id": "manager-send-claim",
    })
    runtime.dispatch(manager, "result_saved", {
        "event_seq": seq, "revision": "0", "request_id": "result-for-send",
        "epoch": lease["epoch"], "lease_token": lease["lease_token"],
        "result_ref": "messenger:action_fixture",
    })
    row = store._connection.execute(
        "SELECT envelope FROM messenger_actions WHERE id='action_fixture'"
    ).fetchone()
    original = json.loads(row[0])
    proof = {
        "event_seq": seq, "revision": "0", "epoch": lease["epoch"],
        "lease_token": lease["lease_token"],
    }
    started = runtime.before_existing_messenger_send(manager, original, proof)
    assert started["action_id"] == "action_fixture"
    assert store._connection.execute(
        "SELECT delivery_state FROM question_coordination"
    ).fetchone()[0] == "unknown"
    with pytest.raises(Busy):
        runtime.before_existing_messenger_send(manager, original, proof)
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE messenger_actions SET status='accepted',receipt=? WHERE id='action_fixture'",
            ('{"receipt":"synthetic-only"}',),
        )
    reconciled = runtime.after_existing_messenger_receipt(manager, started)
    assert reconciled["delivery_state"] == "sent"
    done = runtime.dispatch(manager, "complete", {
        "event_seq": seq, "revision": "0", "request_id": "finished-sent",
        "epoch": lease["epoch"], "lease_token": lease["lease_token"],
    })
    assert done["state"] == "completed"
    assert store._connection.execute(
        "SELECT COUNT(*) FROM messenger_actions"
    ).fetchone()[0] == 1

def test_legacy_draft_send_cannot_bypass_claimed_source_but_neighbor_remains_free(context):
    import hashlib
    import time

    runtime, store, seq, dot, manager, cid, binding = context
    runtime.dispatch(manager, "acquire", {
        "event_seq": seq, "revision": "0", "request_id": "guard-legacy-claim",
    })
    evt = store.workspace_events()["events"][0]
    original_id = str(evt["message_id"])
    conv = store.conversation(cid)
    assert conv is not None
    with store._lock, store._connection:
        for draft_id, source_key in (
            ("draft_claimed_exact", original_id),
            ("draft_unrelated_neighbor", "unrelated-fixture-message"),
        ):
            store._connection.execute(
                "INSERT INTO drafts(user_id,id,conversation_id,body,status,created_at) "
                "VALUES (?,?,?,?,?,?)",
                (manager.user_id, draft_id, cid, "Offline draft", "proposed", time.time()),
            )
            store._connection.execute(
                "INSERT INTO draft_reply_origins VALUES (?,?,?,?,?,?,?,?)",
                (manager.user_id, draft_id, cid, "telegram:123456789",
                 str(conv["peer_id"]), source_key, "1",
                 hashlib.sha256(b"original").hexdigest()),
            )
    with pytest.raises(PermissionError, match="question_requires_original_fenced_messenger_action"):
        store.claim_draft_send("draft_claimed_exact", user_id=manager.user_id)
    assert store.draft("draft_claimed_exact", user_id=manager.user_id).status == "proposed"
    neighbor = store.claim_draft_send("draft_unrelated_neighbor", user_id=manager.user_id)
    assert neighbor.status == "proposed"  # returned pre-transition immutable draft
    assert store.draft("draft_unrelated_neighbor", user_id=manager.user_id).status == "sending"

def test_actual_messenger_approve_requires_question_fence_before_fake_provider_io(tmp_path):
    from universal_userio.mcp_dispatch import UserIOToolDispatcher

    class FakeProvider:
        account_ref = "telegram:899000123"
        operations = {"reply", "history", "read"}
        def __init__(self):
            self.calls = []
        def execute(self, envelope):
            self.calls.append(envelope)
            return {
                "account_ref": self.account_ref,
                "peer_id": envelope["peer_id"],
                "provider_message_id": "93",
                "receipt": "fake-provider-receipt",
            }

    store = SQLiteUserIOStore(tmp_path / "real-userio.sqlite3")
    try:
        provider = FakeProvider()
        service = UserIOService(store, NoGenerator(), NoOutbox())
        service.messenger_providers = {provider.account_ref: provider}
        store.register_account(
            account_id=provider.account_ref, provider="telegram",
            display_name="Fake provider", can_read=True, can_reply=True,
            credential_ref="telegram-qr:offline",
        )
        source_id = provider.account_ref + "|8899:77"
        cid, _ = service.receive(InboxMessage(
            "telegram", source_id, "real-fixture-owner",
            "Please reply", 10.0, peer_id="8899", conversation_kind="direct",
            sender_is_bot=False,
        ), route_id="telegram", account_ref=provider.account_ref)
        with store._lock, store._connection:
            store._connection.execute(
                "UPDATE workspace_events SET eligible=1 WHERE message_id=?",
                (source_id,),
            )
        seq = int(store.workspace_events()["events"][0]["seq"])
        runtime = QuestionRuntime(
            store, key=b"synthetic-backend-messenger-hmac-key-of-32-bytes",
            dot_binding_path=tmp_path / "nonexistent-dot-binding",
        )
        service.question_runtime = runtime
        owner = store.owner()
        manager = UserPrincipal(
            owner.user_id, owner.username, owner.role,
            messenger_accounts=(provider.account_ref,),
        )
        api = UserIOToolDispatcher(store, service)
        proposed = api.dispatch("userio.messenger.propose", {
            "account_ref": provider.account_ref, "conversation_id": cid,
            "source_message_id": source_id, "action": "reply",
            "payload": {"text": "Fixture-approved answer"},
            "request_id": "fixture-reply-request-0000001",
        }, principal=manager)
        assert proposed["ok"] is True, proposed
        action = proposed["action"]
        lease = runtime.dispatch(manager, "acquire", {
            "event_seq": seq, "revision": "0", "request_id": "claim-provider-1",
        })
        runtime.dispatch(manager, "result_saved", {
            "event_seq": seq, "revision": "0", "request_id": "result-provider-1",
            "lease_token": lease["lease_token"], "epoch": lease["epoch"],
            "result_ref": "messenger:" + action["id"],
        })
        approval = {
            "action_id": action["id"], "expected_hash": action["hash"], "confirm": True,
        }
        refused = api.dispatch("userio.messenger.approve", approval, principal=manager)
        assert not refused["ok"] and refused["error"] == "current_question_lease_required"
        assert provider.calls == []
        proof = {
            "event_seq": seq, "revision": "0", "epoch": lease["epoch"],
            "lease_token": lease["lease_token"],
        }
        accepted = api.dispatch("userio.messenger.approve", {
            **approval, "question": proof,
        }, principal=manager)
        assert accepted["ok"] is True, accepted
        assert accepted["action"]["status"] == "accepted"
        assert accepted["action"]["question_reconciliation"]["state"] == "sent"
        assert len(provider.calls) == 1
        assert api.dispatch("userio.messenger.approve", {
            **approval, "question": proof,
        }, principal=manager)["action"]["status"] == "accepted"
        assert len(provider.calls) == 1
        completed = runtime.dispatch(manager, "complete", {
            "event_seq": seq, "revision": "0", "request_id": "completed-provider-1",
            "epoch": lease["epoch"], "lease_token": lease["lease_token"],
        })
        assert completed["receipt_ref"] == "messenger:" + action["id"]
    finally:
        store.close()
