"""Focused integration, expected3s/max30s; no network/credentials/provider RPC.

Detects Python/relay signature drift, guessed projections, scoped account
substitution, denied-vs-uncertain replay, and loss of actual image/voice data.
"""
import io
import json
import hashlib
import hmac
from urllib.error import HTTPError

import pytest

from universal_userio.messenger import Messenger, canonical
from universal_userio.messenger_relay import RelayMessengerProvider, APPROVAL_SCHEMA
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


@pytest.fixture
def case(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'store.db');service=UserIOService(store,object(),object())
    ref='telegram:900000103';peer='-1009000002001'
    store.register_account(account_id=ref,provider='telegram',display_name='Secretary',can_read=True,can_reply=True,credential_ref='relay:current')
    token=tmp_path/'token';token.write_text('fixture-only')
    calls=[];options={}
    def opener(req,**kw):
        path=req.full_url.removeprefix('http://existing-relay');args=json.loads(req.data) if req.data else {}
        if path.endswith('/capabilities'):result={'account_ref':ref,'sender_id':'900000103','operations':['history','download','reply','edit']}
        elif path.endswith('/message'):
            result={'message':{'source':{'account_ref':ref,'peer_id':peer,'conversation_id':args['conversation_id'],
                'message_id':ref+'|'+peer+':77','provider_message_id':'77','channel':'telegram','direction':'incoming',
                'conversation_kind':'group','thread_id':'70','reply_to_provider_message_id':'76'},
                'author':{'id':'42','name':'Actual author'},'text':'actual source','at':10,'edited_at':0,'media':[],
                'content':{'kind':'voice','status':'ready','transcript':'actual transcript'}}}
        elif path.endswith('/execute'):
            calls.append(args)
            if options.get('refusal'):
                raise HTTPError(req.full_url,options['refusal'],'fixture denial',{},io.BytesIO(json.dumps({'error':options.get('error','source_or_approval_not_admitted')}).encode()))
            result={'account_ref':ref,'peer_id':peer,'provider_message_id':'78','receipt':'actual-fixture-receipt'}
        else:raise AssertionError(path)
        return Response(json.dumps(result).encode())
    p=RelayMessengerProvider(service=service,base_url='http://existing-relay',token_file=str(token),
        approval_key='fixture-approval-key',account_ref=ref,authorized_user=store.default_user_id,opener=opener)
    service.messenger_providers[ref]=p
    api=Messenger(store,service,store.owner())
    yield store,service,api,p,ref,peer,calls,options
    store.close()


def test_resolve_real_source_sourcebound_hmac_and_actual_voice(case):
    store,service,api,provider,ref,peer,calls,options=case
    resolved=api.resolve(ref,peer,'77');cid=resolved['conversation_id']
    assert resolved['message']['author']['id']=='42' and resolved['message']['source']['thread_id']=='70'
    assert api.media(ref,cid,ref+'|'+peer+':77')['content']['transcript']=='actual transcript'
    a=api.propose(ref,cid,ref+'|'+peer+':77','reply',{'text':'exact reply'},'new-relay-reply-123456')
    # Provider projection after acceptance may fail. Receipt remains authoritative.
    sent=api.approve(a['id'],a['hash'],True)
    assert sent['status']=='accepted' and len(calls)==1
    packet=calls[0];approval=packet['approval']
    proof=hmac.new(b'fixture-approval-key',(APPROVAL_SCHEMA+'\n').encode()+canonical(approval),hashlib.sha256).hexdigest()
    assert packet['proof']==proof and approval['envelope_sha256']==a['hash']
    assert packet['envelope']['source_binding']['author_id']=='42'
    assert api.approve(a['id'],a['hash'],True)['receipt']==sent['receipt'] and len(calls)==1


@pytest.mark.parametrize('status,error,state',[(403,'source_or_approval_not_admitted','proposed'),
    (409,'source_or_action_conflict','proposed'),(409,'outcome_uncertain','sending')])
def test_provider_denial_or_unknown_keeps_correct_durable_state(case,status,error,state):
    store,service,api,p,ref,peer,calls,options=case
    cid=api.resolve(ref,peer,'77')['conversation_id']
    a=api.propose(ref,cid,ref+'|'+peer+':77','reply',{'text':'exact reply'},'one-relay-denial-123456')
    options.update(refusal=status,error=error)
    with pytest.raises(Exception):api.approve(a['id'],a['hash'],True)
    assert api.get(a['id'])['status']==state and len(calls)==1
    if state=='sending':
        with pytest.raises(ValueError):api.approve(a['id'],a['hash'],True)
        assert len(calls)==1
