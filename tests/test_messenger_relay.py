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


def test_history_omits_unbindable_author_without_guessing_or_scope_relaxation(case):
    import copy
    _,_,_,provider,ref,peer,_,_=case
    actual=provider.message(peer_id=peer,provider_message_id='77',conversation_id='fixture')
    absent=copy.deepcopy(actual);absent['author']['id']=''
    absent['source'].update(provider_message_id='1',message_id=ref+'|'+peer+':1')
    absent['text']='';absent['media']=[]
    def opener(req,**kwargs):
        return Response(json.dumps({'messages':[actual,absent],'next_cursor':'older-1','partial':False}).encode())
    provider.opener=opener
    page=provider.history(peer_id=peer,conversation_id='fixture',limit=25)
    assert len(page['messages'])==1 and page['messages'][0]['author']['id']=='42'
    assert page['partial'] and page['omitted_count']==1 and page['next_cursor']=='older-1'
    with pytest.raises(ValueError,match='provider_author_unavailable'):
        provider._validated_projection(absent,peer,'fixture')
    absent['source']['account_ref']='telegram:900000101'
    with pytest.raises(ValueError,match='scope conflict'):
        provider.history(peer_id=peer,conversation_id='fixture',limit=25)


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



def test_media_read_passes_exact_attachment_index(case):
    store,service,api,provider,ref,peer,calls,options=case
    cid=api.resolve(ref,peer,'77')['conversation_id']
    old=provider.opener;seen=[]
    def opener(req,**kwargs):
        if req.data and req.full_url.endswith('/message'):seen.append(json.loads(req.data))
        return old(req,**kwargs)
    provider.opener=opener
    api.media(ref,cid,ref+'|'+peer+':77',attachment_index=1)
    assert seen[-1]['read_media'] and seen[-1]['attachment_index']==1



def test_real_invocation_survives_normal_provider_reconciliation(case):
    store,service,api,provider,ref,peer,calls,options=case
    import hashlib
    cid='conv_'+hashlib.sha256((store.default_user_id+'\0telegram:'+ref+':'+peer).encode()).hexdigest()[:24]
    projected=provider.message(peer_id=peer,provider_message_id='77',conversation_id=cid)
    projected['source']['invocation']={'mention':False,'reply':True}
    provider.import_projection(projected,peer_id=peer,conversation_id=cid,kind='group')
    result=api.history(ref,cid)['messages'][0]
    assert result['source']['invocation']=={'mention':False,'reply':True}


def test_media_survives_routing_import_and_reconciliation(case):
    store,service,api,provider,ref,peer,calls,options=case
    old=provider.opener
    def opener(req,**kwargs):
        response=old(req,**kwargs)
        result=json.loads(response.getvalue())
        if req.full_url.endswith('/message'):
            result['message']['media']=[
                {'kind':'image','content_type':'image/png','size':90835,
                 'attachment_id':'5361575419839556313','idx':0},
                {'kind':'document','content_type':'application/pdf','size':100,
                 'attachment_id':'second-native-id','idx':1}]
            result['message']['source']['invocation']={'mention':False,'reply':True}
        return Response(json.dumps(result).encode())
    provider.opener=opener
    for _ in range(2):
        resolved=api.resolve(ref,peer,'77')
        message=resolved['message']
        assert [a['attachment_id'] for a in message['media']]==[
            '5361575419839556313','second-native-id']
        assert [a['attachment_index'] for a in message['media']]==[0,1]
        assert message['source']['invocation']=={'mention':False,'reply':True}
        assert message['author']['id']=='42'
        rows=store.attachments_for_message(source='telegram',message_id=ref+'|'+peer+':77')
        assert [(a['idx'],a['kind']) for a in rows]==[(0,'image'),(1,'document'),(2,'telegram_routing')]
    assert len(api.history(ref,resolved['conversation_id'])['messages'])==1


def test_new_file_bytes_travel_only_with_signed_approved_metadata(case):
    import base64
    store,service,api,p,ref,peer,calls,options=case
    cid=api.resolve(ref,peer,'77')['conversation_id']
    p._caps['operations']+=['file','file_upload']
    data=b'New exported document bytes'
    u=api.upload(ref,cid,ref+'|'+peer+':77',filename='export.txt',mime_type='text/plain',
        data_base64=base64.b64encode(data).decode(),sha256=hashlib.sha256(data).hexdigest(),request_id='relay-upload-stage-123456')
    assert not calls
    a=api.propose(ref,cid,ref+'|'+peer+':77','file',{'upload_id':u['upload_id']},'relay-upload-send-123456')
    result=api.approve(a['id'],a['hash'],True)
    assert result['status']=='accepted' and len(calls)==1
    packet=calls[0]
    assert base64.b64decode(packet['upload']['data_base64'])==data
    assert packet['envelope']['payload']['sha256']==hashlib.sha256(data).hexdigest()
    assert packet['approval']['payload_sha256']==hashlib.sha256(canonical(packet['envelope']['payload'])).hexdigest()
    assert 'data_base64' not in packet['envelope']['payload']
    assert api.approve(a['id'],a['hash'],True)==result and len(calls)==1
