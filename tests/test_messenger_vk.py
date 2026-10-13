"""Focused integration: VK reactions/source isolation, expected 5s/max30s."""
import json,io
from dataclasses import replace
import pytest
from universal_userio.channels.vk_personal import VKPersonalClient
from universal_userio.messenger import Messenger
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.account_connections import vk_personal_message

class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

@pytest.fixture
def case(tmp_path,monkeypatch):
    store=SQLiteUserIOStore(tmp_path/'db');service=UserIOService(store,object(),object());calls=[]
    row={'id':8,'conversation_message_id':1,'peer_id':111,'from_id':42,'out':1,'date':1760000000,'text':'actual','reaction_id':0}
    from urllib.parse import parse_qs
    def opener(req,timeout):
        method=req.full_url.rsplit('/',1)[-1];args=parse_qs(req.data.decode());calls.append((method,args))
        if method=='users.get':r=[{'id':42}]
        elif method=='messages.getById':r={'items':[dict(row)]}
        elif method=='messages.getReactionsAssets':r={'reaction_ids':[1,2,3,4]}
        elif method=='messages.sendReaction':
            assert args['cmid']==['1'] and 'message_id' not in args
            row['reaction_id']=int(args['reaction_id'][0]);r=1
        elif method=='messages.deleteReaction':row['reaction_id']=0;r=1
        else:raise AssertionError(method)
        return Response(json.dumps({'response':r}).encode())
    client=VKPersonalClient('fixture-private-token',opener=opener)
    monkeypatch.setattr('universal_userio.account_connections._vk_personal_client',lambda *args,**kw:(client,42))
    store.register_account(account_id='vk:42',provider='vk',display_name='test',can_read=True,can_reply=True,credential_ref='connector:fixture')
    msg=vk_personal_message(account_id='vk:42',peer_id=111,row=row,reconciled=True)
    cid,_=service.receive(msg,route_id='vk',account_ref='vk:42')
    yield Messenger(store,service,store.owner()),cid,row,calls
    store.close()

def propose(api,cid,emoji,rid):
    return api.propose('vk:42',cid,'vk:42|111:8','react',{'emoji':emoji},rid)

def test_generic_vk_live_shape_lifecycle_and_no_replay(case):
    api,cid,row,calls=case
    caps=api.capabilities('vk:42');assert caps['operations']['react']['allowed']
    assert not caps['operations']['typing']['supported']
    assert api.resolve('vk:42','111','8')['message']['source']['provider_message_id']=='8'
    for i,emoji in enumerate(['👍','🔥','']):
        p=propose(api,cid,emoji,'vk-reactions-fixture-'+str(i))
        result=api.approve(p['id'],p['hash'],True);assert result['status']=='accepted'
        api.approve(p['id'],p['hash'],True)
        read=api.resolve('vk:42','111','8')['message']['reaction_info']
        assert read['own_reaction_id']==[4,2,0][i]
    assert len([c for c in calls if c[0] in {'messages.sendReaction','messages.deleteReaction'}])==3
    readonly=Messenger(api.store,api.service,replace(api.principal,read_only=True))
    assert not readonly.capabilities('vk:42')['operations']['react']['allowed']
    with pytest.raises(PermissionError):propose(readonly,cid,'👍','vk-readonly-fixture-1')

def test_vk_source_drift_and_unsupported_reaction_never_mutate(case):
    api,cid,row,calls=case
    p=propose(api,cid,'👍','vk-drift-fixture-1');row['peer_id']=112
    from universal_userio.contracts import DeliveryNotSubmittedError
    with pytest.raises(DeliveryNotSubmittedError):api.approve(p['id'],p['hash'],True)
    assert api.get(p['id'])['status']=='proposed'
    row['peer_id']=111
    p=propose(api,cid,'😎','vk-unsupported-fixture-1')
    with pytest.raises(DeliveryNotSubmittedError):api.approve(p['id'],p['hash'],True)
    assert not any(c[0]=='messages.sendReaction' for c in calls)

def test_native_vk_requires_actual_cmid_and_current_catalog(case):
    api,cid,row,calls=case
    row['conversation_message_id']=None
    p=propose(api,cid,'👍','vk-missing-cmid-fixture-1')
    from universal_userio.contracts import DeliveryNotSubmittedError
    with pytest.raises(DeliveryNotSubmittedError):api.approve(p['id'],p['hash'],True)
    assert not any(c[0]=='messages.sendReaction' for c in calls)

def test_preflight_refusal_is_explicit_not_unknown(case):
    from universal_userio.messenger import dispatch
    api,cid,row,calls=case
    p=propose(api,cid,'👍','vk-preflight-status-fixture-1');row['peer_id']=112
    result=dispatch(api.store,api.service,api.principal,'userio.messenger.approve',
                   {'action_id':p['id'],'expected_hash':p['hash'],'confirm':True})
    assert result['error']=='provider_not_submitted' and result['action_status']=='proposed'
    assert not any(c[0]=='messages.sendReaction' for c in calls)
