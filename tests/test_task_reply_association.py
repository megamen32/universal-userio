from dataclasses import asdict, dataclass
from contextlib import contextmanager
import json
import sqlite3
import time

import pytest

from universal_userio.adapters import inbox_message_from_envelope
from universal_userio.contracts import ReplyDraft
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.personal_tokens import PersonalTokens
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.task_contact_qualification import (
    CatalogContactProof,
    ProviderContactIdentity,
)
from universal_userio.task_mandates import TaskMandates
from universal_userio.task_reply_association import (
    InboundMessageKey,
    TaskReplyAssociation,
    UserIOStoreTaskReplyReaders,
)

TASK = "u53-task"
ACTOR = "u53-actor"
PURPOSE = "Send one authorized task update to the qualified MAX contact"


@dataclass(frozen=True)
class Message:
    provider: str
    account_ref: str
    conversation_id: str
    message_id: str
    reply_to_provider_message_id: str


@dataclass(frozen=True)
class Scope:
    task_id: str
    actor_id: str
    project_id: str
    version: int
    provider: str
    account_ref: str
    conversation_id: str
    contact_id: str = ""
    source_namespace: str = ""
    source_id: str = ""


@dataclass(frozen=True)
class Receipt:
    operation_id: str
    task_id: str
    actor_id: str
    project_id: str
    version: int
    provider: str
    account_ref: str
    conversation_id: str
    provider_message_id: str
    status: str
    contact_id: str
    source_namespace: str
    source_id: str


class PersistedReaders:
    """Reader fixture returning persisted-shaped immutable model rows."""

    def __init__(self, message=None, scopes=(), receipts=()):
        self.message_row = message
        self.scope_rows = tuple(scopes)
        self.receipt_rows = tuple(receipts)
        self.calls = []

    def read_current_message(self, key):
        self.calls.append(("message", key))
        row = self.message_row
        if row is None or (
            row.provider, row.account_ref, row.conversation_id,
            row.message_id,
        ) != (key.provider, key.account_ref, key.conversation_id, key.message_id):
            return None
        return row

    def read_task_scopes(self, message):
        self.calls.append(("scopes", message.message_id))
        return self.scope_rows

    def read_operation_receipts(self, *, provider, account_ref, conversation_id,
                                provider_message_id):
        self.calls.append(("receipts", provider, account_ref, conversation_id,
                           provider_message_id))
        return tuple(row for row in self.receipt_rows
                     if row.provider_message_id == provider_message_id)


class SQLiteReaders:
    """Fresh reader instances rehydrate exact rows from a durable test DB."""

    def __init__(self, path):
        self.path = path
        self.calls = []

    def _read(self, query, args=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(query, args).fetchall()

    def read_current_message(self, key):
        self.calls.append(("message", key))
        rows = self._read("SELECT body FROM message")
        return Message(**json.loads(rows[0][0])) if rows else None

    def read_task_scopes(self, message):
        self.calls.append(("scopes", message.message_id))
        return tuple(Scope(**json.loads(body))
                     for (body,) in self._read("SELECT body FROM scopes"))

    def read_operation_receipts(self, *, provider, account_ref, conversation_id,
                                provider_message_id):
        self.calls.append(("receipts", provider, account_ref, conversation_id,
                           provider_message_id))
        return tuple(Receipt(**json.loads(body)) for (body,) in self._read(
            "SELECT body FROM receipts WHERE provider_message_id=?",
            (provider_message_id,),
        ))


def _base():
    key = InboundMessageKey("telegram", "acct-1", "conv-1", "in-42")
    message = Message("telegram", "acct-1", "conv-1", "in-42", "out-7")
    scope = Scope("task-1", "actor-1", "project-1", 3,
                  "telegram", "acct-1", "conv-1")
    receipt = Receipt("op-1", "task-1", "actor-1", "project-1", 3,
                      "telegram", "acct-1", "conv-1", "out-7", "accepted",
                      "contact-9", "catalog", "source-9")
    return key, message, scope, receipt


MAX_ACCOUNT = "max:task-reply-association"
MAX_BODY = "The authorized task message, sent once."


class MaxCatalogLease:
    def __init__(self, proof, action):
        self.proof = dict(proof)
        self.action = action
        self.binding = None

    def bind(self, binding):
        direct_conflict = any(
            key != "expires_at" and binding.get(key) != value
            for key, value in self.proof.items()
        )
        qualified = binding.get("qualified_contacts")
        batch_conflict = (qualified is not None and not any(
            all(contact.get(key) == value for key, value in self.proof.items()
                if key != "expires_at")
            for contact in qualified
        ))
        rebound_conflict = (self.binding is not None and any(
            key not in binding or binding[key] != value
            for key, value in self.binding.items()
        ))
        if (binding.get("action") != self.action
                or (qualified is None and direct_conflict)
                or batch_conflict or rebound_conflict):
            raise ValueError("test catalog scope mismatch")
        self.binding = dict(binding)

    def assert_current(self, binding):
        if self.binding != dict(binding):
            raise ValueError("test catalog binding changed")


class MaxCatalogOrigin:
    revision = "u53-catalog-revision"

    def resolve_contact(self, *, user_id, task_id, actor_id, contact_ref):
        assert contact_ref == "contact-ref"
        return CatalogContactProof(
            user_id=user_id, task_id=task_id, actor_id=actor_id,
            project_id="project-from-catalog", catalog_namespace="airlockplan.integrators",
            catalog_record_id="integrator-100", catalog_contact_id="contact-200",
            catalog_revision=self.revision, contact_version="contact-v1",
            provider="max", account_ref=MAX_ACCOUNT,
            provider_locator_kind="provider_native_ref",
            provider_locator="provider-contact-200", expires_at=time.time() + 7200,
        )

    def revalidate_contact(self, qualification):
        return (qualification.get("catalog_revision") == self.revision
                and qualification.get("task_id") == TASK
                and qualification.get("actor_id") == ACTOR
                and qualification.get("account_ref") == MAX_ACCOUNT
                and qualification.get("provider") == "max")

    @contextmanager
    def acquire_authority_guard(self, *, proof, action, binding):
        proofs = proof if isinstance(proof, list) else [proof]
        if any(item.get("catalog_revision") != self.revision for item in proofs):
            raise PermissionError("test source revision changed")
        lease = MaxCatalogLease(proofs[0], action)
        lease.bind(binding)
        yield lease


class MaxProviderResolver:
    def resolve_catalog_contact(self, *, account_ref, locator_kind, locator, user_id):
        assert account_ref == MAX_ACCOUNT
        assert locator_kind == "provider_native_ref"
        assert locator == "provider-contact-200"
        return ProviderContactIdentity(
            provider="max", account_ref=MAX_ACCOUNT, peer_id="200",
            peer_kind="direct", route_id="max", display_name="Contact",
        )


class FakeMaxClient:
    def __init__(self):
        self.own_id = "owner-max-1"
        self.profile = {"profile": {"contact": {"id": self.own_id}}}
        self.rows = {}
        self.sent = []

    def send(self, *, chat_id, text):
        message_id = "92001"
        self.rows[message_id] = {
            "id": message_id, "sender": self.own_id, "out": True,
            "time": time.time() * 1000, "text": text,
        }
        self.sent.append((chat_id, message_id, text))
        return {"message_id": message_id}

    def get_message(self, *, chat_id, message_id):
        assert chat_id == "200"
        return self.rows[str(message_id)]


def test_userio_max_outbox_receipt_message_and_reply_join_after_store_reopen(tmp_path):
    db_path = tmp_path / "u53-userio.sqlite3"
    store = SQLiteUserIOStore(db_path)
    store.seed_owner("task-owner", "task-owner-password")
    owner = store.authenticate_credentials("task-owner", "task-owner-password")
    assert owner is not None
    store.register_account(account_id=MAX_ACCOUNT, provider="max", display_name="Test MAX",
                           can_read=True, can_reply=True, credential_ref="fake-max")
    store.bind_channel_route(user_id=owner.user_id, source="max", route_id="max")
    catalog = MaxCatalogOrigin()
    service = UserIOService(
        store, object(), object(), task_catalog_origin=catalog,
        task_contact_provider_resolvers={"max": MaxProviderResolver()},
    )
    max_client = FakeMaxClient()
    service.max_client_for = lambda _conversation, _user_id: max_client

    qualification = service.qualify_task_contact(
        issuer=owner, task_id=TASK, actor_id=ACTOR,
        contact_ref="contact-ref", account_ref=MAX_ACCOUNT,
    )
    mandate = TaskMandates(store, service._task_contact_qualification).issue(
        issuer=owner, task_id=TASK, actor_id=ACTOR, account_ref=MAX_ACCOUNT,
        channel="max", qualification_ids=[qualification["qualification_id"]],
        purpose=PURPOSE, max_sends=1, expires_at=time.time() + 3600,
    )
    authority = TaskMandates(store, service._task_contact_qualification).authenticate_token(
        mandate["token"],
    )
    assert authority is not None
    operation_id = "u53-operation-0001"
    operation = {
        "operation_id": operation_id, "task_id": TASK, "actor_id": ACTOR,
        "account_ref": MAX_ACCOUNT, "channel": "max",
        "conversation_id": qualification["conversation_id"],
        "endpoint_id": qualification["endpoint_id"],
        "recipient_id": qualification["peer_id"],
        "qualification_id": qualification["qualification_id"],
        "catalog_binding_hash": qualification["binding_hash"],
        "purpose": PURPOSE, "body": MAX_BODY, "attachments": [],
    }
    TaskMandates(store, service._task_contact_qualification).reserve(authority, operation)
    draft_id = "u53-draft-0001"
    store.add_draft(ReplyDraft(
        draft_id, qualification["conversation_id"], MAX_BODY, "proposed",
    ), user_id=owner.user_id)

    approved = service._approve_task_mandated_draft(
        principal=authority, operation_id=operation_id, draft_id=draft_id,
    )
    sent_id = "92001"
    assert approved.receipt == sent_id
    assert max_client.sent == [("200", sent_id, MAX_BODY)]
    canonical_outgoing_id = f"{MAX_ACCOUNT}|200:{sent_id}"
    outgoing = store.message(canonical_outgoing_id, source="max", user_id=owner.user_id)
    assert outgoing is not None
    assert outgoing["direction"] == "outgoing"
    assert outgoing["conversation_id"] == qualification["conversation_id"]

    inbound_row = {
        "id": "92002", "sender": "200", "time": time.time() * 1000,
        "text": "I am replying to the exact task message.",
        "link": {"type": "REPLY", "messageId": sent_id},
    }
    from universal_userio.max_ingress import message_envelope
    envelope = message_envelope(inbound_row, chat_id="200", own_id=max_client.own_id)
    assert envelope is not None and envelope["provider_reply_to"] == sent_id
    inbound = inbox_message_from_envelope(envelope, received_at=envelope["received_at"])
    conversation_id, _created = service.receive(
        inbound, route_id="max", user_id=owner.user_id, account_ref=MAX_ACCOUNT,
    )
    assert conversation_id == qualification["conversation_id"]
    inbound_message_id = envelope["message_id"]
    store.close()

    reopened = SQLiteUserIOStore(db_path)
    try:
        result = TaskReplyAssociation(UserIOStoreTaskReplyReaders(
            reopened, user_id=owner.user_id,
        )).resolve(InboundMessageKey(
            "max", MAX_ACCOUNT, conversation_id, inbound_message_id,
        ))
        assert result.status == "linked"
        assert result.operation_id == operation_id
        assert result.task_id == TASK
        assert result.contact_id == "contact-200"
        assert result.source_namespace == "airlockplan.integrators"
        assert result.source_id == "integrator-100"
        assert result.provider_message_id == sent_id
        cross_account = TaskReplyAssociation(UserIOStoreTaskReplyReaders(
            reopened, user_id=owner.user_id,
        )).resolve(InboundMessageKey(
            "max", "max:other-account", conversation_id, inbound_message_id,
        ))
        assert cross_account.status == "denied"

        readonly = PersonalTokens(reopened).create(
            owner.user_id, "u53-readonly", scope="read_only",
        )
        principal = reopened.authenticate_token(readonly["token"])
        assert principal is not None and principal.read_only
        surface = UserIOMcpSurface(
            reopened, UserIOService(reopened, object(), object()),
        )

        inbox = surface.dispatch(
            "userio.inbox.list_new", {"limit": 50}, principal=principal,
        )
        unread = next(item for item in inbox["messages"]
                      if item["message_id"] == inbound_message_id)
        assert unread["task_reply_association"]["status"] == "linked"
        assert unread["task_reply_association"]["task_id"] == TASK
        assert unread["task_reply_association"]["operation_id"] == operation_id

        history = surface.dispatch(
            "userio.conversation.get", {"conversation_id": conversation_id},
            principal=principal,
        )
        incoming = next(item for item in history["conversation"]["messages"]
                        if item["message_id"] == inbound_message_id)
        assert incoming["task_reply_association"]["provider_message_id"] == sent_id

        channels = surface.dispatch(
            "userio.channels.read", {"channel": "max", "chat_id": conversation_id},
            principal=principal,
        )
        channel_incoming = next(item for item in channels["chat"]["messages"]
                                if item["message_id"] == inbound_message_id)
        assert channel_incoming["task_reply_association"]["contact_id"] == "contact-200"

        denied_send = surface.dispatch(
            "userio.channels.send_draft",
            {"chat_id": conversation_id, "text": "not allowed"},
            principal=principal,
        )
        assert denied_send == {"ok": False, "error": "read_only_token"}
        assert max_client.sent == [("200", sent_id, MAX_BODY)]
    finally:
        reopened.close()


def test_userio_store_reader_uses_canonical_message_and_fails_closed_without_task_receipt(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "u53-store-reader.sqlite3")
    try:
        store.seed_owner("reader-owner", "reader-owner-password")
        owner = store.authenticate_credentials("reader-owner", "reader-owner-password")
        assert owner is not None
        store.register_account(account_id=MAX_ACCOUNT, provider="max", display_name="Test MAX",
                               can_read=True, can_reply=True, credential_ref="fake-max")
        store.bind_channel_route(user_id=owner.user_id, source="max", route_id="max")
        service = UserIOService(store, object(), object())
        from universal_userio.max_ingress import message_envelope
        envelope = message_envelope({
            "id": "93001", "sender": "200", "time": time.time() * 1000,
            "text": "Reply with no task receipt.",
            "link": {"type": "REPLY", "messageId": "92001"},
        }, chat_id="200", own_id="owner-max-1")
        assert envelope is not None
        inbound = inbox_message_from_envelope(envelope, received_at=envelope["received_at"])
        conversation_id, _created = service.receive(
            inbound, route_id="max", user_id=owner.user_id, account_ref=MAX_ACCOUNT,
        )
        readers = UserIOStoreTaskReplyReaders(store, user_id=owner.user_id)
        canonical = readers.read_current_message(InboundMessageKey(
            "max", MAX_ACCOUNT, conversation_id, envelope["message_id"],
        ))
        assert canonical is not None
        assert canonical.reply_to_provider_message_id == "92001"
        assert readers.read_task_scopes(canonical) == ()
        assert readers.read_operation_receipts(
            provider="max", account_ref=MAX_ACCOUNT,
            conversation_id=conversation_id, provider_message_id="92001",
        ) == ()
        result = TaskReplyAssociation(readers).resolve(InboundMessageKey(
            "max", MAX_ACCOUNT, conversation_id, envelope["message_id"],
        ))
        assert result.status == "denied"
        assert result.reason == "orphan_reply_target"
    finally:
        store.close()


def test_exact_reply_joins_persisted_accepted_operation_with_full_scope():
    key, message, scope, receipt = _base()
    resolver = TaskReplyAssociation(PersistedReaders(message, [scope], [receipt]))

    result = resolver.resolve(key)

    assert result.status == "linked"
    assert result.task_id == "task-1"
    assert result.operation_id == "op-1"
    assert result.contact_id == "contact-9"
    assert result.source_namespace == "catalog"
    assert result.source_id == "source-9"
    assert result.provider_message_id == "out-7"


def test_exact_sent_receipt_survives_process_restart_without_duplicate_link(tmp_path):
    key, message, scope, receipt = _base()
    receipt = receipt.__class__(**{**receipt.__dict__, "status": "sent"})
    db_path = tmp_path / "receipts.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            "CREATE TABLE message (body TEXT NOT NULL);"
            "CREATE TABLE scopes (body TEXT NOT NULL);"
            "CREATE TABLE receipts (provider_message_id TEXT NOT NULL, body TEXT NOT NULL);"
        )
        db.execute("INSERT INTO message VALUES (?)", (json.dumps(asdict(message)),))
        db.execute("INSERT INTO scopes VALUES (?)", (json.dumps(asdict(scope)),))
        db.execute("INSERT INTO receipts VALUES (?,?)",
                   (receipt.provider_message_id, json.dumps(asdict(receipt))))

    first_result = TaskReplyAssociation(SQLiteReaders(db_path)).resolve(key)
    restarted_reader = SQLiteReaders(db_path)
    result = TaskReplyAssociation(restarted_reader).resolve(key)

    assert first_result.status == "linked"
    assert result.status == "linked"
    assert result.operation_id == "op-1"
    assert len([call for call in restarted_reader.calls if call[0] == "receipts"]) == 1


def test_duplicate_reads_of_same_immutable_receipt_link_once():
    key, message, scope, receipt = _base()
    result = TaskReplyAssociation(
        PersistedReaders(message, [scope], [receipt, receipt]),
    ).resolve(key)

    assert result.status == "linked"
    assert result.operation_id == "op-1"


def test_ambiguous_conversation_stays_unresolved_without_exact_parent_receipt():
    key, message, scope, receipt = _base()
    other = Scope("task-2", "actor-1", "project-1", 3,
                  "telegram", "acct-1", "conv-1")
    resolver = TaskReplyAssociation(PersistedReaders(message, [scope, other], []))

    result = resolver.resolve(key)

    assert result.status == "unresolved"
    assert result.reason == "no_exact_replied_to_operation"
    assert result.task_id is None


def test_exact_reply_receipt_selects_one_task_from_multi_task_conversation():
    key, message, scope, receipt = _base()
    other = Scope("task-2", "actor-1", "project-1", 3,
                  "telegram", "acct-1", "conv-1")

    result = TaskReplyAssociation(
        PersistedReaders(message, [scope, other], [receipt]),
    ).resolve(key)

    assert result.status == "linked"
    assert result.task_id == "task-1"


def test_receipt_for_task_without_canonical_source_scope_is_denied():
    key, message, _, receipt = _base()
    other = Scope("task-2", "actor-1", "project-1", 3,
                  "telegram", "acct-1", "conv-1")

    result = TaskReplyAssociation(
        PersistedReaders(message, [other], [receipt]),
    ).resolve(key)

    assert result.status == "denied"
    assert result.reason == "task_source_scope_not_found"


def test_unknown_receipt_never_links_or_gets_reissued():
    key, message, scope, receipt = _base()
    unknown = receipt.__class__(**{**receipt.__dict__, "status": "UNKNOWN"})

    result = TaskReplyAssociation(PersistedReaders(message, [scope], [unknown])).resolve(key)

    assert result.status == "unresolved"
    assert result.reason == "operation_outcome_unknown"
    assert result.operation_id is None


@pytest.mark.parametrize("field,value", [
    ("account_ref", "acct-other"),
    ("provider", "matrix"),
    ("conversation_id", "conv-other"),
    ("actor_id", "actor-other"),
    ("project_id", "project-other"),
    ("version", 4),
])
def test_cross_scope_receipt_is_denied(field, value):
    key, message, scope, receipt = _base()
    bad = receipt.__class__(**{**receipt.__dict__, field: value})

    result = TaskReplyAssociation(PersistedReaders(message, [scope], [bad])).resolve(key)

    assert result.status == "denied"
    assert result.reason == "operation_scope_mismatch"


def test_orphan_message_and_mismatched_canonical_ids_deny():
    key, message, scope, receipt = _base()
    assert TaskReplyAssociation(PersistedReaders()).resolve(key).status == "denied"
    orphan = TaskReplyAssociation(PersistedReaders(message, [], [])).resolve(key)
    assert orphan.status == "denied"
    assert orphan.reason == "orphan_reply_target"
    wrong = message.__class__(**{**message.__dict__, "message_id": "different"})
    assert TaskReplyAssociation(PersistedReaders(wrong, [scope], [receipt])).resolve(key).status == "denied"


def test_endpoint_or_name_and_last_message_are_never_used_as_fallback():
    key, message, scope, receipt = _base()
    later = receipt.__class__(**{**receipt.__dict__, "provider_message_id": "out-8"})
    result = TaskReplyAssociation(PersistedReaders(message, [scope], [later])).resolve(key)

    assert result.status == "unresolved"
    assert result.reason == "no_exact_replied_to_operation"
