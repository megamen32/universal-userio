"""Telegram reply is bound to one stored message and approved exactly once."""
from universal_userio.contracts import InboxMessage
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class ReplyOutbox:
    def __init__(self):
        self.calls = []

    def send_reply(self, **_kwargs):
        return "legacy-send"

    def reply_message(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ok": True,
            "account_id": kwargs["account_ref"],
            "peer_id": kwargs["chat_id"],
            "message_id": "78",
            "reply_to_id": kwargs["message_id"],
            "thread_id": kwargs["thread_id"],
            "text": kwargs["text"],
            "date": 123,
            "out": True,
            "slot": "nikita",
        }


def test_telegram_reply_capability_approves_and_projects_exact_reply(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "store.sqlite3")
    outbox = ReplyOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    account = "telegram:900000101"
    peer = "900000300"
    store.register_account(account_id=account, provider="telegram", display_name="Nikita",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:nikita")
    conversation_id, _ = service.receive(InboxMessage(
        source="telegram", message_id=f"{account}|{peer}:77", sender="900000300",
        body="original", received_at=100, direction="incoming", peer_id=peer,
        conversation_kind="direct", provider_thread_id="70",
    ), route_id="telegram", account_ref=account)
    api = UserIOToolDispatcher(store, service)
    principal = store.owner()

    capabilities = api.dispatch("userio.messenger.capabilities", {"account_ref": account}, principal=principal)
    assert capabilities["operations"]["reply"] == {
        "supported": True, "allowed": True, "reason": ""
    }

    proposal = api.dispatch("userio.messenger.propose", {
        "account_ref": account, "conversation_id": conversation_id,
        "source_message_id": f"{account}|{peer}:77", "action": "reply",
        "payload": {"text": "answer"}, "request_id": "telegram-reply-request-0001",
    }, principal=principal)
    assert proposal["ok"] is True, proposal
    action = proposal["action"]
    assert action["status"] == "proposed"
    assert outbox.calls == []
    approved = api.dispatch("userio.messenger.approve", {
        "action_id": action["id"], "expected_hash": action["hash"], "confirm": True,
    }, principal=principal)
    assert approved["ok"] is True, approved
    accepted = approved["action"]
    assert accepted["status"] == "accepted"
    assert len(outbox.calls) == 1
    assert outbox.calls[0] == {
        "chat_id": peer,
        "account_ref": account,
        "message_id": "77",
        "expected_text": "original",
        "thread_id": "70",
        "text": "answer",
        "request_id": action["envelope"]["action_id"],
    }
    messages = store.conversation(conversation_id)["messages"]
    sent = next(message for message in messages if message["direction"] == "outgoing")
    assert sent["message_id"] == f"{account}|{peer}:78"
    assert sent["body"] == "answer"
    stored = store.message(sent["message_id"], source="telegram")
    assert stored["provider_reply_to"] == "77"

    replayed = api.dispatch("userio.messenger.approve", {
        "action_id": action["id"], "expected_hash": action["hash"], "confirm": True,
    }, principal=principal)
    assert replayed["action"]["receipt"] == accepted["receipt"]
    assert len(outbox.calls) == 1
    store.close()
