"""Telegram reply is bound to one stored message and approved exactly once."""
from universal_userio.contracts import InboxMessage
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class ReplyOutbox:
    def __init__(self):
        self.calls = []
        self.admin_prepares = []
        self.admin_calls = []
        self.lose_admin_response_once = False

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

    def telegram_admin_prepare(self, **kwargs):
        self.admin_prepares.append(kwargs["operation"])
        return {"account_id": kwargs["account_ref"], "operation": kwargs["operation"],
                "peer_id": kwargs.get("peer_id", ""), "resolved": dict(kwargs["payload"])}

    def telegram_admin_execute(self, **kwargs):
        self.admin_calls.append(kwargs["request_id"])
        if self.lose_admin_response_once:
            self.lose_admin_response_once = False
            raise TimeoutError("response lost after provider accepted")
        return {"ok": True, "account_id": kwargs["account_ref"],
                "operation": kwargs["operation"], "peer_id": "-100700",
                "conversation_kind": "channel", "title": kwargs["payload"].get("title", ""),
                "status": "complete", "receipt": "admin-receipt"}


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


def test_telegram_admin_proposals_are_exact_account_bound_and_deduplicated(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "store.sqlite3")
    outbox = ReplyOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    account = "telegram:900000101"
    store.register_account(account_id=account, provider="telegram", display_name="Nikita",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:nikita")
    from universal_userio.mcp_surface import UserIOMcpSurface
    api = UserIOMcpSurface(store, service)
    principal = store.owner()
    args = {"account_ref": account, "operation": "channel_create",
            "payload": {"title": "Test private channel", "about": ""},
            "request_id": "telegram-admin-request-001"}
    proposal = api.dispatch("userio.messenger.telegram_admin.propose", args, principal=principal)
    assert proposal["ok"] is True, proposal
    action = proposal["action"]
    assert action["status"] == "proposed" and action["envelope"]["channel"] == "telegram"
    assert action["envelope"]["payload"]["private"] is True
    assert outbox.admin_calls == []
    approved = api.dispatch("userio.messenger.approve", {
        "action_id": action["id"], "expected_hash": action["hash"], "confirm": True,
    }, principal=principal)
    assert approved["ok"] and approved["action"]["status"] == "accepted"
    again = api.dispatch("userio.messenger.telegram_admin.propose", args, principal=principal)
    assert again["action"]["id"] == action["id"]
    assert len(outbox.admin_prepares)==1 and len(outbox.admin_calls)==1
    assert not api.dispatch("userio.messenger.telegram_admin.propose", {
        **args, "operation": "channel_delete", "payload": {"peer_id": "-100700"}
    }, principal=principal)["ok"]
    from universal_userio.personal_tokens import PersonalTokens
    tokens=PersonalTokens(store)
    admin=tokens.create(store.default_user_id,"admin secretary",scope="telegram_admin",account_refs=[account])
    admin_principal=store.authenticate_token(admin["token"])
    assert admin_principal.telegram_admin_accounts==(account,)
    admin_caps=api.dispatch("userio.messenger.capabilities",{"account_ref":account},principal=admin_principal)
    assert admin_caps["telegram_admin"]["allowed"]
    manifest={tool["name"] for tool in UserIOMcpSurface(store,service).tool_manifest(principal=admin_principal)["tools"]}
    assert "userio.messenger.telegram_admin.propose" in manifest
    assert "userio.messenger.propose" not in manifest
    sender=tokens.create(store.default_user_id,"normal sender",scope="messenger_actions",account_refs=[account])
    sender_principal=store.authenticate_token(sender["token"])
    sender_caps=api.dispatch("userio.messenger.capabilities",{"account_ref":account},principal=sender_principal)
    assert sender_caps["telegram_admin"]["allowed"] is False
    assert sender_caps["telegram_admin"]["reason"]=="telegram_admin_scope_required"
    store.close()


def test_telegram_admin_recovers_a_lost_http_response_without_new_action(tmp_path):
    store=SQLiteUserIOStore(tmp_path/"store.sqlite3")
    outbox=ReplyOutbox();outbox.lose_admin_response_once=True
    service=UserIOService(store,object(),object(),telegram_outbox=outbox)
    account="telegram:900000101"
    store.register_account(account_id=account,provider="telegram",display_name="Nikita",
        can_read=True,can_reply=True,credential_ref="telegram-qr:nikita")
    from universal_userio.mcp_surface import UserIOMcpSurface
    api=UserIOMcpSurface(store,service);principal=store.owner()
    proposal=api.dispatch("userio.messenger.telegram_admin.propose",{
        "account_ref":account,"operation":"channel_create",
        "payload":{"title":"Recovery channel","about":""},
        "request_id":"telegram-admin-recovery-001"},principal=principal)
    action=proposal["action"]
    approval={"action_id":action["id"],"expected_hash":action["hash"],"confirm":True}
    assert not api.dispatch("userio.messenger.approve",approval,principal=principal)["ok"]
    assert api.dispatch("userio.messenger.get",{"action_id":action["id"]},principal=principal)["action"]["status"]=="sending"
    recovered=api.dispatch("userio.messenger.approve",approval,principal=principal)
    assert recovered["ok"] and recovered["action"]["status"]=="accepted"
    assert len(outbox.admin_calls)==2 and len(set(outbox.admin_calls))==1
    store.close()
