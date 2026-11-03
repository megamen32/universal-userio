"""Source-only fake-provider acceptance for original human-approved question action.

All network is an isolated loopback HTTP test, never a real UserIO provider.
"""
import hashlib
import json
import re
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from universal_userio.contracts import InboxMessage, UserPrincipal
from universal_userio.http_api import handler
from universal_userio.messenger import Messenger
from universal_userio.question_action_consent import QuestionActionConsent
from universal_userio.question_coordination import AccessDenied, Busy
from universal_userio.question_runtime import QuestionRuntime
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class NoGeneration:
    def generate(self, *_args, **_kwargs):
        raise AssertionError("No LLM in source-only tests")


class NoOutbox:
    def send_reply(self, **_kwargs):
        raise AssertionError("No network provider send")


class FakeProvider:
    account_ref = "telegram:123456789"
    operations = {"reply", "history"}

    def __init__(self):
        self.calls = 0
        self.lose_response = False

    def execute(self, e):
        self.calls += 1
        if self.lose_response:
            raise TimeoutError("fake provider response intentionally unknown")
        return {
            "account_ref": e["account_ref"], "peer_id": e["peer_id"],
            "provider_message_id": "201",
            "receipt": "offline-receipt-only",
        }


@pytest.fixture
def fixture(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "source-only.sqlite3")
    provider = FakeProvider()
    service = UserIOService(store, NoGeneration(), NoOutbox())
    service.messenger_providers = {provider.account_ref: provider}
    store.register_account(
        account_id=provider.account_ref, provider="telegram",
        display_name="Dot fixture", can_read=True, can_reply=True,
        credential_ref="telegram-qr:offline",
    )
    source_id = provider.account_ref + "|12345:77"
    cid, accepted = service.receive(
        InboxMessage(
            "telegram", source_id, "owner-offline",
            "Exact human question", 1700000000.0,
            peer_id="12345", conversation_kind="direct",
            sender_is_bot=False,
        ),
        route_id="telegram", account_ref=provider.account_ref,
    )
    assert accepted
    seq = int(store.workspace_events()["events"][0]["seq"])
    binding = tmp_path / "dot-binding.json"
    binding.write_text(json.dumps({
        "enabled": True, "owner": "12345", "room": "offline-room",
        "botAccountId": provider.account_ref,
    }), encoding="utf-8")
    binding.chmod(0o600)
    runtime = QuestionRuntime(
        store, key=hashlib.sha256(
            b"userio-question-lease:fixture-global-service-not-real").digest(),
        dot_binding_path=binding,
    )
    service.question_runtime = runtime
    consent = QuestionActionConsent(store, service, runtime)
    service.question_action_consent = consent
    dot = store.owner()  # Existing service principal, NEVER a human
    human = UserPrincipal(store.default_user_id, "owner", "owner")
    lease = runtime.dispatch(dot, "acquire", {
        "event_seq": seq, "revision": "0", "request_id": "acquire-original-1",
    })
    action = Messenger(store, service, dot).propose(
        provider.account_ref, cid, source_id, "reply",
        {"text": "dots:\nExact approved answer"}, "original-send-request-001",
    )
    runtime.dispatch(dot, "result_saved", {
        "event_seq": seq, "revision": "0", "request_id": "save-original-1",
        "lease_token": lease["lease_token"], "epoch": lease["epoch"],
        "result_ref": "messenger:" + action["id"],
    })
    yield store, service, consent, provider, dot, human, action, lease, seq, binding
    store.close()


def proof(lease, seq):
    return {
        "event_seq": seq, "revision": "0",
        "epoch": lease["epoch"], "lease_token": lease["lease_token"],
    }


def approve_human(consent, human, action):
    snap = consent.preview(human, action["id"])
    return consent.record(
        human, action_id=action["id"],
        expected_hash=action["hash"],
        expected_body_sha256=snap["body_sha256"],
        question_id=snap["question_id"], epoch=snap["epoch"],
    )


def test_raw_dot_confirm_does_not_send_without_real_human_consent(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    with pytest.raises(AccessDenied, match="original_human_consent_required"):
        Messenger(store, service, dot).approve(
            action["id"], action["hash"], True, question=proof(lease, seq))
    assert provider.calls == 0
    assert Messenger(store, service, dot).get(action["id"])["status"] == "proposed"
    assert store._connection.execute(
        "SELECT delivery_state FROM question_coordination"
    ).fetchone()[0] == "none"
    with pytest.raises(AccessDenied, match="interactive_owner_approval_required"):
        consent.preview(dot, action["id"])


def test_human_approval_binds_original_action_and_one_provider_receipt(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    approval = approve_human(consent, human, action)
    assert approval["status"] == "approved" and approval["approval_ref"].startswith("qca_")
    assert approve_human(consent, human, action)["duplicate"] is True
    result = consent.send_approved_original(
        dot, action_id=action["id"], expected_hash=action["hash"], **proof(lease, seq))
    assert provider.calls == 1
    assert result["status"] == "accepted"
    assert result["question_delivery_state"] == "sent"
    assert result["receipt_ref"] == "messenger:" + action["id"]
    assert result["source_message_id"] == provider.account_ref + "|12345:77"
    assert result["provider_message_id"] == "201"
    assert result["message_sha256"]
    again = consent.send_approved_original(
        dot, action_id=action["id"], expected_hash=action["hash"], **proof(lease, seq))
    assert again["receipt_ref"] == result["receipt_ref"]
    assert provider.calls == 1
    terminal = service.question_runtime.dispatch(dot, "complete", {
        "event_seq": seq, "revision": "0", "request_id": "complete-original-1",
        "epoch": lease["epoch"], "lease_token": lease["lease_token"],
    })
    assert terminal["state"] == "completed"


def test_wrong_epoch_and_changed_action_refuse_before_provider(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    snap = consent.preview(human, action["id"])
    with pytest.raises(Exception):
        consent.record(
            human, action_id=action["id"],
            expected_hash="changed", expected_body_sha256=snap["body_sha256"],
            question_id=snap["question_id"], epoch=snap["epoch"])
    with pytest.raises(Exception):
        consent.send_approved_original(
            dot, action_id=action["id"], expected_hash=action["hash"],
            event_seq=seq, revision="0", epoch=lease["epoch"] + 1,
            lease_token=lease["lease_token"])
    assert provider.calls == 0


def test_unknown_after_original_provider_call_is_never_replayed(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    approve_human(consent, human, action)
    provider.lose_response = True
    with pytest.raises(Exception):
        consent.send_approved_original(
            dot, action_id=action["id"], expected_hash=action["hash"], **proof(lease, seq))
    assert provider.calls == 1
    assert store._connection.execute(
        "SELECT delivery_state FROM question_coordination"
    ).fetchone()[0] == "unknown"
    with pytest.raises(Busy):
        consent.send_approved_original(
            dot, action_id=action["id"], expected_hash=action["hash"], **proof(lease, seq))
    assert provider.calls == 1


def test_interactive_session_csrf_and_dot_route_never_auto_approve(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, binding = fixture
    owner_session = store.create_oauth_session(store.default_user_id)
    app = ThreadingHTTPServer(("127.0.0.1", 0),
                               handler(service, token="fixture-global-service-not-real"))
    # Existing handler constructs a new QuestionRuntime. Its bound source is
    # the same DB, but the fixture's dot file must be passed to it.
    service.question_runtime.dot_binding_path = binding
    service.question_action_consent.runtime = service.question_runtime
    service.question_runtime.human_consent = service.question_action_consent
    t = Thread(target=app.serve_forever, daemon=True)
    t.start()
    root = "http://127.0.0.1:" + str(app.server_port)
    url = root + "/v1/questions/actions/" + action["id"] + "/review"
    try:
        def fetch(url, body=None, headers=None):
            req = Request(
                url, method="POST" if body is not None else "GET",
                data=body, headers=headers or {},
            )
            try:
                with urlopen(req, timeout=5) as response:
                    return response.status, response.read().decode("utf-8")
            except HTTPError as response:
                return response.status, response.read().decode("utf-8")

        owner_cookie = {"Cookie": "userio_web_session=" + owner_session}
        assert fetch(url)[0] == 401
        assert fetch(url, headers={"Authorization": "Bearer fixture-global-service-not-real"})[0] == 401
        status, page = fetch(url, headers=owner_cookie)
        assert status == 200
        assert "Exact approved answer" in page
        csrf = re.search(r'name="csrf" value="([a-f0-9]+)"', page).group(1)
        snap = service.question_action_consent.preview(human, action["id"])
        params = {
            "action_hash": action["hash"], "body_sha256": snap["body_sha256"],
            "question_id": snap["question_id"], "epoch": str(snap["epoch"]),
            "csrf": csrf, "decision": "approve",
        }
        approval_url = root + "/v1/questions/actions/" + action["id"] + "/approve"
        headers = {
            **owner_cookie, "Origin": root,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        assert fetch(approval_url, urlencode(params).encode(),
                     {**headers, "Origin": "https://evil.invalid"})[0] == 403
        assert fetch(approval_url, urlencode({**params, "csrf": "wrong"}).encode(),
                     headers)[0] == 409
        assert provider.calls == 0
        status, receipt = fetch(approval_url, urlencode(params).encode(), headers)
        assert status == 200
        assert json.loads(receipt)["status"] == "approved"
        assert provider.calls == 0  # A human click records consent, not a send.
        dispatch_url = root + "/v1/questions/approved-original-send"
        payload = {"action_id": action["id"], "expected_hash": action["hash"],
                   **proof(lease, seq)}
        status, sent = fetch(dispatch_url, json.dumps(payload).encode(), {
            "Authorization": "Bearer fixture-global-service-not-real",
            "Content-Type": "application/json",
        })
        assert status == 200, sent
        assert json.loads(sent)["receipt_ref"] == "messenger:" + action["id"]
        assert provider.calls == 1
    finally:
        app.shutdown()
        app.server_close()
        t.join(timeout=4)


def test_service_token_cannot_bypass_question_claim_with_generic_mcp_approve(fixture):
    store, service, consent, provider, dot, human, _action, _lease, _seq, _ = fixture
    account = provider.account_ref
    conversation = store.create_conversation(
        "telegram", "owner-secondary", account_ref=account,
        peer_id="12345", conversation_kind="direct",
    )
    # A second incoming source in the same bound dot chat is still pending.
    source_id = account + "|12345:78"
    service.receive(InboxMessage(
        "telegram", source_id, "owner-offline", "Second question", 1700000100.0,
        peer_id="12345", conversation_kind="direct",
        sender_is_bot=False,
    ), route_id="telegram", account_ref=account)
    # Do not create a question-level claim for this distinct message.
    new_conversation = store.message(source_id, source="telegram")["conversation_id"]
    action = Messenger(store, service, dot).propose(
        account, new_conversation, source_id, "reply",
        {"text": "Unapproved follow-up"}, "unclaimed-dot-send-0001",
    )
    assert service.question_runtime._question_for_action(dot, action["envelope"]) is None
    result = service.question_runtime
    with pytest.raises(AccessDenied, match="dot_original_question_claim_required"):
        Messenger(store, service, dot).approve(action["id"], action["hash"], True)
    assert provider.calls == 0


def test_account_permission_revoked_after_human_consent_blocks_send(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    approve_human(consent, human, action)
    store.register_account(
        account_id=provider.account_ref, provider="telegram",
        display_name="Revoked fixture", can_read=True, can_reply=False,
        credential_ref="telegram-qr:offline",
    )
    with pytest.raises((PermissionError, AccessDenied)):
        consent.send_approved_original(
            dot, action_id=action["id"], expected_hash=action["hash"], **proof(lease, seq))
    assert provider.calls == 0


def test_source_edit_after_human_review_blocks_original_send(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    approve_human(consent, human, action)
    with store._lock, store._connection:
        store._connection.execute(
            "UPDATE messages SET body=?,edited_at=? WHERE user_id=? AND message_id=?",
            ("source changed after review", 1700000200.0,
             store.default_user_id, provider.account_ref + "|12345:77"),
        )
    with pytest.raises((ValueError, PermissionError, AccessDenied)):
        consent.send_approved_original(
            dot, action_id=action["id"], expected_hash=action["hash"], **proof(lease, seq))
    assert provider.calls == 0


def test_second_reaction_action_cannot_bypass_claimed_question_send_fence(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    # This fake provider has a separate react capability for the test.
    provider.operations = {"reply", "react", "history"}
    source_id = provider.account_ref + "|12345:77"
    cid = store.message(source_id, source="telegram")["conversation_id"]
    with pytest.raises(PermissionError, match="question_non_reply_action_requires_review"):
        Messenger(store, service, dot).safe_action(
            provider.account_ref, cid, source_id, "react",
            {"emoji": "OK"}, "second-provider-effect-0001",
        )
    assert provider.calls == 0


def test_dot_can_stage_same_original_action_without_sending(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    staged = consent.propose_dot_original(
        dot, event_seq=seq, revision="0", epoch=lease["epoch"],
        lease_token=lease["lease_token"],
        request_id="original-send-request-001",
        body="dots:\nExact approved answer",
    )
    assert staged["status"] == "awaiting_human_approval"
    assert staged["sent"] is False
    assert staged["action_id"] == action["id"]
    assert staged["source_message_id"] == provider.account_ref + "|12345:77"
    assert provider.calls == 0
    # Same ID with another body cannot silently overwrite the reviewed action.
    with pytest.raises(ValueError):
        consent.propose_dot_original(
            dot, event_seq=seq, revision="0", epoch=lease["epoch"],
            lease_token=lease["lease_token"],
            request_id="original-send-request-001",
            body="different message without new consent",
        )
    assert provider.calls == 0


def test_historical_accepted_receipt_without_human_record_is_not_approval(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, _ = fixture
    approve_human(consent, human, action)
    consent.send_approved_original(
        dot, action_id=action["id"], expected_hash=action["hash"],
        **proof(lease, seq),
    )
    assert provider.calls == 1
    with store._lock, store._connection:
        store._connection.execute(
            "DELETE FROM question_action_consents WHERE user_id=? AND action_id=?",
            (dot.user_id, action["id"]),
        )
    with pytest.raises(AccessDenied, match="original_human_consent_required"):
        consent.project_original_receipt(dot, action["id"], seq, "0", lease["epoch"])
    assert provider.calls == 1


def test_http_unknown_receipt_explicitly_refuses_replay(fixture):
    store, service, consent, provider, dot, human, action, lease, seq, binding = fixture
    approve_human(consent, human, action)
    provider.lose_response = True
    app = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler(service, token="fixture-global-service-not-real"),
    )
    service.question_runtime.dot_binding_path = binding
    service.question_action_consent.runtime = service.question_runtime
    service.question_runtime.human_consent = service.question_action_consent
    t = Thread(target=app.serve_forever, daemon=True)
    t.start()
    try:
        url = ("http://127.0.0.1:" + str(app.server_port)
               + "/v1/questions/approved-original-send")
        body = json.dumps({
            "action_id": action["id"], "expected_hash": action["hash"],
            **proof(lease, seq),
        }).encode("utf-8")
        def submit():
            req = Request(url, data=body, method="POST", headers={
                "Authorization": "Bearer fixture-global-service-not-real",
                "Content-Type": "application/json",
            })
            try:
                with urlopen(req, timeout=5) as result:
                    return result.status, json.loads(result.read())
            except HTTPError as result:
                return result.code, json.loads(result.read())
        code, first = submit()
        assert code == 202
        assert first == {
            "status": "unknown", "retryable": False,
            "action_id": action["id"],
            "error": "original_delivery_unknown_read_original_receipt",
        }
        assert provider.calls == 1
        code, repeat = submit()
        assert code == 409 and repeat["retryable"] is False
        assert provider.calls == 1
    finally:
        app.shutdown()
        app.server_close()
        t.join(timeout=4)
