"""Fast unit: account-scoped AI exclusion; expected2/max20s, no provider calls."""
import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class NoAI:
    def suggest(self, **kwargs):
        raise AssertionError("excluded mail must never reach AI")


def receive(service, account, message_id):
    message = InboxMessage("gmail:" + account, message_id, "sender@example.test", "OTP 123456", 1.0)
    conv, accepted = service.receive(message, route_id="gmail-read-only", account_ref=account)
    assert accepted
    return conv, message


def test_exclusion_before_first_message_keeps_raw_mail_and_no_workspace(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "state.db")
    service = UserIOService(store, NoAI(), object())
    inbound = []
    service.add_inbound_listener(lambda *args: inbound.append(args))
    store.set_account_ai_enabled("airlock", False)
    conv, message = receive(service, "airlock", "mail-first")
    assert store.conversation(conv)["messages"][0]["body"] == "OTP 123456"
    assert store.evaluate_workspace_chat(conversation_id=conv)["account_ai_disabled"]
    assert store.workspace_events()["events"] == []
    assert inbound == []
    with pytest.raises(ValueError, match="AI processing is disabled"):
        service.propose(conv, message)
    assert store.conversation_summary_work(conv) is None


def test_other_account_and_user_are_unaffected_and_policy_persists(tmp_path):
    path = tmp_path / "state.db"
    store = SQLiteUserIOStore(path)
    store.set_account_ai_enabled("airlock", False)
    assert not store.account_ai_disabled("personal")
    assert not store.account_ai_disabled("airlock", user_id="another-user")
    service = UserIOService(store, NoAI(), object())
    conv, _ = receive(service, "personal", "mail-other")
    assert store.evaluate_workspace_chat(conversation_id=conv)["allowed"]
    assert len(store.workspace_events()["events"]) == 1
    store.close()
    reopened = SQLiteUserIOStore(path)
    assert reopened.account_ai_disabled("airlock")


def test_excluding_existing_account_invalidates_pending_events(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "state.db")
    service = UserIOService(store, NoAI(), object())
    conv, _ = receive(service, "airlock", "old-mail")
    assert len(store.workspace_events()["events"]) == 1
    store.set_account_ai_enabled("airlock", False)
    assert store.workspace_events()["events"] == []
    store.set_workspace_chat_rule(conversation_id=conv, action="allow")
    assert not store.evaluate_workspace_chat(conversation_id=conv)["allowed"]
    store.set_account_ai_enabled("airlock", True)
    assert store.workspace_events()["events"] == []  # No historical replay.
