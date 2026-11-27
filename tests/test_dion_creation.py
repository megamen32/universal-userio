"""DION creation through the native adapter and durable scoped approval journal."""
from dataclasses import replace
import pytest

from universal_userio import dion_chat, messenger_dion
from universal_userio.dion_chat import DionChatClient, DionChatUncertain
from universal_userio.messenger import Messenger
from universal_userio.scoped_permissions import validate_grants, enforce
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore

ACCOUNT = "dion:org:owner"

@pytest.fixture
def case(tmp_path, monkeypatch):
    store = SQLiteUserIOStore(tmp_path / "db.sqlite")
    user = store.default_user_id
    service = UserIOService(store, object(), object())
    store.register_account(account_id=ACCOUNT, provider="dion", display_name="Fixture",
        can_read=True, can_reply=True, credential_ref="connector:fixture", enabled=True, user_id=user)
    store.bind_channel_route(user_id=user,source="dion",route_id="dion")
    class Client:
        calls=[]
        uncertain=False
        row=None
        def create_conversation(self, *, title, kind, participant_ids):
            self.calls.append((title,kind,participant_ids))
            if self.uncertain: raise DionChatUncertain("fixture timeout")
            self.row={"conversation_id":"new-peer-"+kind.lower(),"conversation_type":kind,"name":title}
            return self.row
        def conversation(self, peer):
            assert self.row["conversation_id"]==peer
            return self.row
        def list_conversations(self, **kwargs):
            return {"conversations":[self.row]}
        def history(self, peer, **kwargs):
            return {"messages":[]}
    client=Client()
    for module in (dion_chat,messenger_dion):
        monkeypatch.setattr(module,"_client",lambda *a:(client,{"provider_user_id":"owner"}))
    try:
        yield store,service,Messenger(store,service,store.owner()),client
    finally: store.close()

@pytest.mark.parametrize("operation,kind",[("group_create","GROUP"),("channel_create","CHANNEL")])
def test_creation_confirmation_idempotency_and_empty_chat_projection(case,operation,kind):
    store,service,api,client=case
    assert api.capabilities(ACCOUNT)["dion_admin"]["allowed"]
    a=api.propose_dion_creation(ACCOUNT,operation,{"title":"Fixture"}, "dion-create-fixture-001")
    assert client.calls==[]
    assert a["status"]=="proposed"
    with pytest.raises(ValueError,match="confirmation"): api.approve(a["id"],a["hash"],False)
    done=api.approve(a["id"],a["hash"],True)
    assert done["status"]=="accepted" and done["receipt"]["readback_verified"]
    assert done["receipt"]["conversation_type"]==kind
    assert client.calls==[("Fixture",kind,())]
    assert api.approve(a["id"],a["hash"],True)==done
    assert api.propose_dion_creation(ACCOUNT,operation,{"title":"Fixture"}, "dion-create-fixture-001")==done
    with pytest.raises(ValueError,match="idempotency_conflict"):
        api.propose_dion_creation(ACCOUNT,operation,{"title":"Another"},"dion-create-fixture-001")
    chats=store.conversations(source="dion",user_id=api.user)
    assert len(chats)==1 and chats[0]["peer_id"]=="new-peer-"+kind.lower()
    dion_chat.poll_dion(service,{"user_id":api.user,"account_id":ACCOUNT})
    chats=store.conversations(source="dion",user_id=api.user)
    assert len(chats)==1
    full=store.conversation(chats[0]["id"],user_id=api.user)
    assert full["conversation_kind"]==kind.lower()
    assert len(full["messages"])==0

def test_unknown_creation_is_not_replayed(case):
    _,_,api,client=case
    a=api.propose_dion_creation(ACCOUNT,"group_create",{"title":"Fixture"},"dion-create-unknown-001")
    client.uncertain=True
    with pytest.raises(DionChatUncertain): api.approve(a["id"],a["hash"],True)
    assert api.get(a["id"])["status"]=="sending"
    with pytest.raises(ValueError,match="delivery_uncertain"):
        api.approve(a["id"],a["hash"],True)
    assert len(client.calls)==1

def test_scope_requires_explicit_creation_permission(case):
    store,service,api,client=case
    base={"account_ref":ACCOUNT,"access":"read_write","chats":None}
    plain=replace(api.principal,messenger_accounts=(ACCOUNT,),messenger_grants=(base,))
    restricted=Messenger(store,service,plain)
    assert not restricted.capabilities(ACCOUNT)["dion_admin"]["allowed"]
    with pytest.raises(PermissionError):
        restricted.propose_dion_creation(ACCOUNT,"group_create",{"title":"Fixture"},"dion-create-restricted-001")
    allowed=validate_grants(store,api.user,[{**base,"dion_admin":True}])
    principal=replace(plain,messenger_grants=tuple(allowed))
    scoped=Messenger(store,service,principal)
    arguments={"account_ref":ACCOUNT,"operation":"channel_create","payload":{"title":"Fixture"},
               "request_id":"dion-create-scoped-001"}
    enforce(store,principal,"userio.messenger.dion_admin.propose",arguments)
    a=scoped.propose_dion_creation(**arguments)
    enforce(store,principal,"userio.messenger.approve",{"action_id":a["id"]})
    with pytest.raises(PermissionError):
        enforce(store,plain,"userio.messenger.approve",{"action_id":a["id"]})
    manifest=UserIOMcpSurface(store,service).tool_manifest(principal=principal)
    assert any(t["name"]=="userio.messenger.dion_admin.propose" for t in manifest["tools"])
    with pytest.raises(ValueError):
        validate_grants(store,api.user,[{**base,"access":"read_only","dion_admin":True}])
    assert client.calls==[]

def test_create_wire_contract_and_wrong_type_receipt():
    class Broker: pass
    client=DionChatClient(Broker(),ACCOUNT,opener=object())
    calls=[]
    def request(method,path,**kwargs):
        calls.append((method,path,kwargs))
        return {"conversation_id":"id-1","conversation_type":"CHANNEL"}
    client._request=request
    assert client.create_conversation(title="Fixture",kind="CHANNEL")["conversation_id"]=="id-1"
    assert calls==[("POST","/v1/conversations",{"body":{"name":"Fixture","conversation_type":"CHANNEL","members_id":[]},"side_effect":True})]
    with pytest.raises(DionChatUncertain):
        client.create_conversation(title="Fixture",kind="GROUP")
