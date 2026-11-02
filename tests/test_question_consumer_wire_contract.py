"""Consumer-visible question protocol on exact installed UserIO main, offline only.

This fixture is deliberately separate from test_question_coordination.py.
It creates a synthetic UserIO SQLite store and does not perform any sends.
"""

from universal_userio.contracts import InboxMessage, UserPrincipal
from universal_userio.mcp_catalog import QUESTION_TOOL_SPECS
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.question_runtime import QuestionRuntime
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class ForbiddenGenerator:
    def generate(self, *_args, **_kwargs):
        raise AssertionError("model must not execute in protocol contract test")


class ForbiddenSend:
    def send_reply(self, **_kwargs):
        raise AssertionError("provider must not send in protocol contract test")


def test_exact_mcp_question_namespace_fields_and_legacy_aliases_denied():
    tools = {spec.name: spec for spec in QUESTION_TOOL_SPECS}
    prefix = "userio.messenger.question."
    assert set(tools) == {
        prefix + operation for operation in (
            "acquire", "renew", "pause", "handoff", "resume",
            "result_saved", "reconcile", "complete", "audit",
        )
    }
    assert tools[prefix + "acquire"].input_schema["required"] == [
        "event_seq", "revision", "request_id",
    ]
    assert tools[prefix + "reconcile"].input_schema["required"] == [
        "event_seq", "revision", "request_id",
    ]
    for name, spec in tools.items():
        properties = set(spec.input_schema["properties"])
        assert {"source_revision", "worker_id", "owner", "holder", "tenant",
                "account_ref", "conversation_id", "message_id"}.isdisjoint(properties), name
        assert spec.input_schema["additionalProperties"] is False
    approve = tools[prefix + "result_saved"].input_schema
    assert approve["required"] == [
        "event_seq", "revision", "lease_token", "epoch", "request_id", "result_ref",
    ]
    assert prefix + "reconcile_delivery" not in tools
    assert "userio.question.acquire" not in tools


def test_production_shape_manager_claim_and_canonical_source_binding(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "real-userio.sqlite3")
    try:
        account = "telegram:123456789"
        peer = "12345"
        source_id = account + "|" + peer + ":77"
        store.register_account(
            account_id=account, provider="telegram", display_name="Fixture bot",
            can_read=True, can_reply=True, credential_ref="telegram-qr:fixture",
        )
        service = UserIOService(store, ForbiddenGenerator(), ForbiddenSend())
        conversation_id, accepted = service.receive(
            InboxMessage(
                "telegram", source_id, "fixture-human",
                "Task question", 1700000000.0, conversation_kind="direct",
                peer_id=peer, sender_is_bot=False,
            ),
            route_id="telegram", account_ref=account,
        )
        assert accepted is True
        saved = store.workspace_events()["events"]
        event = next(row for row in saved if row["message_id"] == source_id)
        assert event["conversation_id"] == conversation_id
        assert event["source"] == "telegram"
        assert event["account_ref"] == account
        # Workspace preview has no edited_at; consumers must read current
        # persisted source revision instead of inventing one from the preview.
        assert "edited_at" not in event
        seq = int(event["seq"])
        principal = UserPrincipal(
            store.default_user_id, "owner", "owner",
            messenger_accounts=(account,),
        )
        service.question_runtime = QuestionRuntime(
            store, key=b"offline-fixture-question-hmac-key-32-bytes-long",
            dot_binding_path=tmp_path / "absent-dot-binding",
        )
        surface = UserIOMcpSurface(store, service)
        names = {t["name"] for t in surface.tool_manifest(principal=principal)["tools"]}
        assert "userio.messenger.question.acquire" in names
        assert "userio.question.acquire" not in names
        args = {"event_seq": seq, "revision": "0",
                "request_id": "consumer-contract-acquire-1"}
        response = surface.dispatch(
            "userio.messenger.question.acquire", args, principal=principal,
        )
        assert response["ok"] is True
        assert set(response) == {"ok", "question"}
        question = response["question"]
        assert question["holder"] == "manager"
        assert question["state"] == "active"
        assert question["epoch"] == 1
        assert question["question_id"]
        assert question["lease_token"].startswith("qcl_")
        assert question["result_ref"] is None
        assert question["lease_expires_at"] > 0
        assert surface.dispatch(
            "userio.messenger.question.acquire",
            {"event_seq": seq, "source_revision": "0",
             "request_id": "wrong-field-name"},
            principal=principal,
        )["ok"] is False
        # The wire reconcile operation never triggers an outgoing provider
        # action. Without the original journal reference it must refuse.
        no_receipt = surface.dispatch(
            "userio.messenger.question.reconcile",
            {"event_seq": seq, "revision": "0",
             "request_id": "reconcile-without-send"},
            principal=principal,
        )
        assert no_receipt["ok"] is False
        assert no_receipt["error"] == "no_delivery_operation"
        readonly = UserPrincipal(
            store.default_user_id, "owner", "owner", read_only=True,
        )
        assert "userio.messenger.question.acquire" not in {
            t["name"] for t in surface.tool_manifest(principal=readonly)["tools"]
        }
        assert surface.dispatch(
            "userio.messenger.question.acquire",
            {"event_seq": seq, "revision": "0",
             "request_id": "read-only-forbidden"},
            principal=readonly,
        )["ok"] is False
        # The global service principal is NOT a manager: it maps to dot,
        # which requires a verified existing dot binding. No role override.
        service_principal_attempt = surface.dispatch(
            "userio.messenger.question.acquire",
            {"event_seq": seq, "revision": "0",
             "request_id": "service-cannot-claim-as-manager"},
            principal=store.owner(),
        )
        assert service_principal_attempt == {
            "ok": False, "error": "verified_dot_binding_unavailable",
        }
        assert store._connection.execute(
            "SELECT COUNT(*) FROM question_coordination"
        ).fetchone()[0] == 1
    finally:
        store.close()
