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
    try:
        tokens.create(store.default_user_id,"obsolete scope",scope="telegram_admin",account_refs=[account])
    except ValueError as error:
        assert str(error)=="unsupported token scope"
    else:
        raise AssertionError("Telegram administration must be a right on a regular token")
    admin=tokens.create(store.default_user_id,"secretary",scope="scoped_messenger",grants=[
        {"account_ref":account,"access":"read_only","chats":None,"telegram_admin":True},
    ])
    admin_principal=store.authenticate_token(admin["token"])
    assert admin["scope"]=="scoped_messenger"
    assert admin_principal.messenger_grants[0]["telegram_admin"] is True
    admin_caps=api.dispatch("userio.messenger.capabilities",{"account_ref":account},principal=admin_principal)
    assert admin_caps["telegram_admin"]["allowed"]
    manifest={tool["name"] for tool in UserIOMcpSurface(store,service).tool_manifest(principal=admin_principal)["tools"]}
    assert "userio.messenger.telegram_admin.propose" in manifest
    assert "userio.messenger.propose" not in manifest
    assert "userio.messenger.approve" in manifest
    denied_reply=api.dispatch("userio.messenger.propose",{
        "account_ref":account,"conversation_id":"outside","source_message_id":"message-1",
        "action":"reply","payload":{"text":"must remain denied"},"request_id":"telegram-admin-deny-001",
    },principal=admin_principal)
    assert denied_reply["error"]=="chat_read_only_token"
    other_account="telegram:900000102"
    store.register_account(account_id=other_account,provider="telegram",display_name="Other",
        can_read=True,can_reply=True,credential_ref="telegram-qr:other")
    assert api.dispatch("userio.messenger.telegram_admin.propose",{
        **args,"account_ref":other_account,"request_id":"telegram-admin-other-001",
    },principal=admin_principal)["error"]=="chat_or_account_outside_token_scope"
    sender=tokens.create(store.default_user_id,"normal sender",scope="messenger_actions",account_refs=[account])
    sender_principal=store.authenticate_token(sender["token"])
    sender_caps=api.dispatch("userio.messenger.capabilities",{"account_ref":account},principal=sender_principal)
    assert sender_caps["telegram_admin"]["allowed"] is False
    assert sender_caps["telegram_admin"]["reason"]=="telegram_admin_permission_required"
    store.close()


def test_scoped_telegram_admin_obeys_selected_chat_scope(tmp_path):
    store=SQLiteUserIOStore(tmp_path/"store.sqlite3")
    account="telegram:900000101"
    store.register_account(account_id=account,provider="telegram",display_name="Nikita",
        can_read=True,can_reply=True,credential_ref="telegram-qr:nikita")
    allowed=store.create_conversation("telegram","700",account_ref=account,peer_id="700",conversation_kind="group")
    denied=store.create_conversation("telegram","701",account_ref=account,peer_id="701",conversation_kind="group")
    from universal_userio.personal_tokens import PersonalTokens
    from universal_userio.mcp_surface import UserIOMcpSurface
    tokens=PersonalTokens(store)
    scoped=tokens.create(store.default_user_id,"group manager",scope="scoped_messenger",grants=[
        {"account_ref":account,"access":"read_only","chats":[
            {"conversation_id":allowed["id"],"access":"read_only"},
        ],"telegram_admin":True},
    ])
    principal=store.authenticate_token(scoped["token"])
    service=UserIOService(store,object(),object(),telegram_outbox=ReplyOutbox())
    api=UserIOMcpSurface(store,service)
    invite={"account_ref":account,"operation":"group_invite","payload":{
        "conversation_id":allowed["id"],"participant_id":"123456"},"request_id":"group-invite-allowed-001"}
    assert api.dispatch("userio.messenger.telegram_admin.propose",invite,principal=principal)["ok"]
    outside={**invite,"payload":{**invite["payload"],"conversation_id":denied["id"]},
        "request_id":"group-invite-denied-001"}
    assert api.dispatch("userio.messenger.telegram_admin.propose",outside,principal=principal)["error"]=="telegram_admin_chat_outside_token_scope"
    create={"account_ref":account,"operation":"channel_create","payload":{"title":"Should be denied","about":""},
        "request_id":"channel-create-denied-001"}
    assert api.dispatch("userio.messenger.telegram_admin.propose",create,principal=principal)["error"]=="telegram_admin_create_requires_account_scope"
    store.close()


def test_scoped_token_reads_and_downloads_only_its_exact_telegram_chat(tmp_path,monkeypatch):
    from universal_userio.adapters import UnifiedChannels
    from universal_userio.contracts import ChannelFile
    from universal_userio.mcp_surface import UserIOMcpSurface
    from universal_userio.mcp_transport import json_rpc_response
    from universal_userio.personal_tokens import PersonalTokens

    store=SQLiteUserIOStore(tmp_path/"store.sqlite3")
    service=UserIOService(store,object(),object())
    account="telegram:900000101"
    other_account="telegram:900000102"
    store.register_account(account_id=account,provider="telegram",display_name="Nikita",
        can_read=True,can_reply=True,credential_ref="telegram-qr:nikita")
    store.register_account(account_id=other_account,provider="telegram",display_name="Other",
        can_read=True,can_reply=True,credential_ref="telegram-qr:other")
    message_id=f"{account}|700:55"
    conversation_id,_=service.receive(InboxMessage(
        source="telegram",message_id=message_id,sender="700",body="document attached",
        received_at=100,direction="incoming",peer_id="700",conversation_kind="direct"),
        route_id="telegram",account_ref=account)
    other_message=f"{other_account}|701:56"
    service.receive(InboxMessage(
        source="telegram",message_id=other_message,sender="701",body="outside account",
        received_at=101,direction="incoming",peer_id="701",conversation_kind="direct"),
        route_id="telegram",account_ref=other_account)

    class SyntheticAdapter:
        def read(self,*,chat_id=None,message_id=None,**_kwargs):
            return {"chat":store.conversation(chat_id,user_id=store.default_user_id)}
        def download_for_ai(self,*,file_ref,max_bytes):
            assert file_ref==message_id and max_bytes>0
            return ChannelFile("notes.txt","text/plain",b"Name,Role\nAda,Secretary\n")
        def download(self,*,file_ref):
            return self.download_for_ai(file_ref=file_ref,max_bytes=1024)

    monkeypatch.setattr(UnifiedChannels,"adapter",lambda _self,_channel:SyntheticAdapter())
    token=PersonalTokens(store).create(store.default_user_id,"secretary",scope="scoped_messenger",grants=[
        {"account_ref":account,"access":"read_only","chats":[
            {"conversation_id":conversation_id,"access":"read_only"},
        ],"telegram_admin":False},
    ])
    principal=store.authenticate_token(token["token"])
    api=UserIOMcpSurface(store,service)
    manifest={tool["name"] for tool in api.tool_manifest(principal=principal)["tools"]}
    assert "userio.channels.read" in manifest and "userio.channels.download" in manifest
    assert "userio.channels.send_draft" not in manifest
    read=api.dispatch("userio.channels.read",{
        "channel":"telegram","chat_id":conversation_id,
    },principal=principal)
    assert read["ok"] and read["chat"]["id"]==conversation_id
    rpc=json_rpc_response(api,{
        "jsonrpc":"2.0","id":1,"method":"tools/call","params":{
            "name":"userio.channels.download","arguments":{
                "file_ref":"telegram:"+message_id,"mode":"mcp_resource"}},
    },principal=principal)
    assert rpc["result"]["isError"] is False
    assert "Ada" in rpc["result"]["structuredContent"]["file"]["text"]
    assert any(block["type"]=="resource" for block in rpc["result"]["content"])
    denied=json_rpc_response(api,{
        "jsonrpc":"2.0","id":2,"method":"tools/call","params":{
            "name":"userio.channels.download","arguments":{
                "file_ref":"telegram:"+other_message,"mode":"mcp_resource"}},
    },principal=principal)
    assert denied["result"]["isError"] is True
    assert denied["result"]["structuredContent"]["error"]=="chat_or_account_outside_token_scope"
    denied_write=api.dispatch("userio.channels.send_draft",{
        "chat_id":conversation_id,"text":"not permitted",
    },principal=principal)
    assert denied_write["error"]=="chat_read_only_token"
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
