from universal_userio.contracts import InboxMessage
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class ButtonOutbox:
    def __init__(self):
        self.prepares = []
        self.clicks = []
        self.lose_response = False

    def prepare_button_click(self, **kwargs):
        self.prepares.append(kwargs)
        return {"ok": True, "account_id": kwargs["account_ref"],
                "peer_id": kwargs["peer_id"], "message_id": kwargs["message_id"],
                "button_text": kwargs["button_text"], "callback_data_base64": "AQID"}

    def button_click(self, **kwargs):
        self.clicks.append(kwargs)
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError("response lost after provider acceptance")
        return {"ok": True, "account_id": kwargs["account_ref"],
                "peer_id": kwargs["peer_id"], "message_id": kwargs["message_id"],
                "button_text": kwargs["button_text"], "row": kwargs["row"],
                "column": kwargs["column"], "status": "accepted",
                "provider_receipt": "telegram-button-receipt",
                "callback_answer": {"class_name": "MessagesBotCallbackAnswer"}}


def setup(tmp_path):
    store = SQLiteUserIOStore(tmp_path / "store.sqlite3")
    outbox = ButtonOutbox()
    service = UserIOService(store, object(), object(), telegram_outbox=outbox)
    account, peer = "telegram:900000101", "900000300"
    store.register_account(account_id=account, provider="telegram", display_name="QA",
                           can_read=True, can_reply=True, credential_ref="telegram-qr:qa")
    conversation_id, _ = service.receive(InboxMessage(
        source="telegram", message_id=f"{account}|{peer}:77", sender=peer,
        body="Choose an option", received_at=100, direction="incoming", peer_id=peer,
        conversation_kind="group",
    ), route_id="telegram", account_ref=account)
    return store, outbox, UserIOToolDispatcher(store, service), store.owner(), account, peer, conversation_id


def args(account, conversation_id, request_id="telegram-button-request-0001"):
    return {"account_ref": account, "conversation_id": conversation_id,
            "source_message_id": f"{account}|900000300:77", "row": 1, "column": 0,
            "button_text": "Continue", "request_id": request_id}


def test_click_button_is_account_and_message_bound_and_replay_reads_receipt(tmp_path):
    store, outbox, api, principal, account, peer, conversation_id = setup(tmp_path)
    request = args(account, conversation_id)
    first = api.dispatch("userio.messenger.button_click", request, principal=principal)
    assert first["ok"] is True, first
    action = first["action"]
    assert action["status"] == "accepted"
    assert action["receipt"]["receipt"] == "telegram-button-receipt"
    assert action["envelope"]["payload"]["callback_data_base64"] == "AQID"
    assert len(outbox.prepares) == len(outbox.clicks) == 1
    assert outbox.clicks[0]["account_ref"] == account
    assert outbox.clicks[0]["peer_id"] == peer
    assert outbox.clicks[0]["message_id"] == "77"
    replay = api.dispatch("userio.messenger.button_click", request, principal=principal)
    assert replay["action"]["receipt"] == action["receipt"]
    assert len(outbox.prepares) == len(outbox.clicks) == 1
    conflict = api.dispatch("userio.messenger.button_click",
                            {**request, "button_text": "Different"}, principal=principal)
    assert conflict["ok"] is False and conflict["error"] == "idempotency_conflict"
    store.close()


def test_unknown_button_submission_is_held_and_never_replayed(tmp_path):
    store, outbox, api, principal, account, _peer, conversation_id = setup(tmp_path)
    outbox.lose_response = True
    request = args(account, conversation_id, "telegram-button-unknown-0001")
    unknown = api.dispatch("userio.messenger.button_click", request, principal=principal)
    assert unknown["ok"] is False
    assert len(outbox.clicks) == 1
    retry_same_identity = api.dispatch("userio.messenger.button_click", request, principal=principal)
    assert retry_same_identity["ok"] is True
    assert retry_same_identity["action"]["status"] == "sending"
    assert len(outbox.clicks) == 1
    store.close()


def test_scoped_actor_can_only_click_its_granted_chat(tmp_path):
    from universal_userio.personal_tokens import PersonalTokens

    store, outbox, api, _owner, account, peer, conversation_id = setup(tmp_path)
    token = PersonalTokens(store).create(store.default_user_id, "button actor", scope="scoped_messenger",
        grants=[{"account_ref":account,"access":"read_write","chats":[
            {"conversation_id":conversation_id,"access":"read_write"}],"telegram_admin":False}])
    principal = store.authenticate_token(token["token"])
    allowed = api.dispatch("userio.messenger.button_click", args(account, conversation_id,
        "telegram-button-scoped-0001"), principal=principal)
    assert allowed["ok"] is True, allowed
    other = store.create_conversation("telegram", peer, user_id=store.default_user_id,
        account_ref=account, peer_id="900000301", route_id="telegram",
        conversation_kind="group")["id"]
    denied = api.dispatch("userio.messenger.button_click", args(account, other,
        "telegram-button-scoped-0002"), principal=principal)
    assert denied["ok"] is False and denied["error"] == "chat_or_account_outside_token_scope"
    assert len(outbox.clicks) == 1
    store.close()
