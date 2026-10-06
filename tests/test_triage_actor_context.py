from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Generator:
    def generate(self, **kwargs):
        return []

    def triage_with_context(self, **kwargs):
        self.input = kwargs
        return dict(decision="silent", importance=0.1, confidence=1.0,
                    urgency="none", reason_codes=[], reason_ru="Обычное сообщение",
                    action_required=False, action_summary="", deadline_at=None,
                    suggested_replies=[], safety_override=False, policy_version="test")


class Outbox:
    def send_reply(self, **kwargs):
        raise AssertionError("No outbound replies")


def register(store, account_id, name, user_id=None):
    store.register_account(account_id=account_id, provider="telegram",
                           display_name=name, can_read=True, can_reply=True,
                           credential_ref="test-ref", user_id=user_id)


def test_private_incoming_resolves_distinct_accounts_without_changing_raw_label(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "state.sqlite3")
    register(store, "telegram:100", "Никита")
    register(store, "telegram:200", "Секретарь")
    generator = Generator()
    service = UserIOService(store, generator, Outbox())
    conversation_id, _ = service.receive(
        InboxMessage("telegram", "200:1", "Никита (старый контакт)", "Результат работы", 1,
                     direction="incoming", conversation_kind="direct", peer_id="200"),
        account_ref="telegram:100", route_id="telegram",
    )
    seq = store.workspace_events()["events"][0]["seq"]
    result = service.triage_workspace_event(event_seq=seq, request_id="actor-test")
    assert generator.input["actor_context"] == {
        "sender_account_id": "telegram:200", "sender_display_name": "Секретарь",
        "receiver_account_id": "telegram:100", "receiver_display_name": "Никита",
    }
    assert generator.input["latest_message"].sender == "Никита (старый контакт)"
    assert store.conversation(conversation_id)["messages"][0]["sender"] == "Никита (старый контакт)"
    assert result["triage"]["decision"] == "silent"  # Identity never forces a notification.


def test_group_and_foreign_accounts_never_supply_a_private_sender_identity(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "state.sqlite3")
    register(store, "telegram:100", "Никита")
    other, _ = store.create_user("other", "test-password")
    register(store, "telegram:200", "Foreign private name", other.user_id)
    service = UserIOService(store, Generator(), Outbox())
    event = dict(source="telegram", direction="incoming", conversation_kind="direct",
                 peer_id="200", account_ref="telegram:100")
    assert service._triage_actor_context(event, store._user(None)) is None
    register(store, "telegram:200", "Секретарь")
    assert service._triage_actor_context({**event, "conversation_kind": "group"}, store._user(None)) is None
    assert service._triage_actor_context({**event, "direction": "outgoing"}, store._user(None)) is None
    assert service._triage_actor_context({**event, "peer_id": "100"}, store._user(None)) is None
