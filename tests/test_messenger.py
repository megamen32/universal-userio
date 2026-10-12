"""Focused integration: account/source/action parity; expected 3s, maximum 30s.

Detects catalog divergence, sender substitution, stale-source approval and
ambiguous mutation replay. No external services, models or provider sends.
"""
import json
from dataclasses import replace

import pytest

from universal_userio.contracts import InboxMessage
from universal_userio.mcp_dispatch import UserIOToolDispatcher
from universal_userio.mcp_surface import UserIOMcpSurface
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


class Provider:
    account_ref = 'telegram:900000103'
    operations = {'reply', 'edit', 'react', 'sticker', 'file', 'typing', 'history', 'download'}
    def __init__(self): self.calls = []; self.fail = False
    def execute(self, envelope):
        self.calls.append(envelope)
        if self.fail: raise TimeoutError('uncertain provider acceptance')
        return {'account_ref': self.account_ref, 'peer_id': envelope['peer_id'],
                'provider_message_id': '900719925474099311', 'receipt': 'owned-receipt'}


@pytest.fixture
def case(tmp_path):
    store = SQLiteUserIOStore(tmp_path / 'store.db')
    provider = Provider()
    service = UserIOService(store, object(), object())
    service.messenger_providers = {provider.account_ref: provider}
    store.register_account(account_id=provider.account_ref, provider='telegram', display_name='Secretary',
        can_read=True, can_reply=True, credential_ref='relay:existing')
    source = InboxMessage('telegram', '-10042:77', 'speaker42', 'real source', 10,
        peer_id='-10042', conversation_kind='group', attachments=({'kind':'telegram_routing',
        'provider_ref': json.dumps({'author_id':'42', 'author_name':'Real speaker', 'reply_to_msg_id':'76', 'reply_to_top_id':'70'})},))
    cid, _ = service.receive(source, route_id='telegram', account_ref=provider.account_ref)
    yield store, service, provider, cid, source
    store.close()


def dispatch(case, name, args, *, readonly=False, legacy=False):
    store, service, *_ = case
    principal = replace(store.owner(), read_only=readonly)
    api = UserIOMcpSurface(store, service) if legacy else UserIOToolDispatcher(store, service)
    return api.dispatch('userio.messenger.'+name, args, principal=principal)


@pytest.mark.parametrize('legacy', [False, True])
def test_effective_caps_never_upgrade_readonly_or_other_account(case, legacy):
    p = case[2]
    result = dispatch(case, 'capabilities', {'account_ref': p.account_ref}, readonly=True, legacy=legacy)
    assert result['ok'] and result['account_ref'] == p.account_ref
    assert result['operations']['read']['allowed']
    assert result['operations']['reply'] == {'supported':True, 'allowed':False, 'reason':'read_only_token'}
    assert not dispatch(case, 'capabilities', {'account_ref':'telegram:900000101'}, legacy=legacy)['ok']


def test_paged_history_exact_source_author_thread_and_opaque_id(case):
    store, service, provider, cid, source = case
    for n in range(78, 81): service.receive(replace(source, message_id=f'-10042:{n}', received_at=n),
        route_id='telegram', account_ref=provider.account_ref)
    args = {'account_ref':provider.account_ref, 'conversation_id':cid, 'limit':2}
    page = dispatch(case, 'history', args)
    assert page['ok'] and len(page['messages']) == 2 and page['partial']
    earlier = dispatch(case, 'history', {**args, 'cursor':page['next_cursor']})
    assert [m['source']['provider_message_id'] for m in earlier['messages']] == ['77','78']
    msg = earlier['messages'][0]
    assert msg['author']['id']=='42' and msg['source']['thread_id']=='70'
    assert msg['source']['account_ref']==provider.account_ref and msg['source']['peer_id']=='-10042'
    assert msg['source']['reply_to_provider_message_id']=='76' and provider.calls==[]


def proposal(case):
    p, cid = case[2:4]
    args = {'account_ref':p.account_ref, 'conversation_id':cid, 'source_message_id':p.account_ref+'|-10042:77',
            'action':'reply', 'payload':{'text':'exact reply'}, 'request_id':'owned-intent-12345678'}
    result = dispatch(case, 'propose', args)
    assert result['ok'], result
    return args, result['action']


def test_exact_approval_durable_receipt_and_repeated_call_never_resends(case):
    args, action = proposal(case)
    assert case[2].calls == []
    assert not dispatch(case,'approve',{'action_id':action['id'],'expected_hash':'wrong','confirm':True})['ok']
    approval={'action_id':action['id'],'expected_hash':action['hash'],'confirm':True}
    sent=dispatch(case,'approve',approval)
    assert sent['ok'] and sent['action']['status']=='accepted'
    assert dispatch(case,'approve',approval)['action']['receipt']==sent['action']['receipt']
    assert len(case[2].calls)==1 and case[2].calls[0]['source_provider_message_id']=='77'
    assert case[2].calls[0]['thread_id']=='70'
    assert not dispatch(case,'propose',{**args,'payload':{'text':'different'}})['ok']


def test_uncertain_not_replayed_even_after_reopen(case):
    args, action = proposal(case); case[2].fail=True
    approval={'action_id':action['id'],'expected_hash':action['hash'],'confirm':True}
    assert not dispatch(case,'approve',approval)['ok']
    assert dispatch(case,'get',{'action_id':action['id']})['action']['status']=='sending'
    assert not dispatch(case,'approve',approval)['ok'] and len(case[2].calls)==1


def test_stale_source_blocks_before_provider_and_readonly_cannot_propose(case):
    args, action = proposal(case)
    store, service, provider, cid, source=case
    service.receive(replace(source, body='changed', edited_at=99), route_id='telegram', account_ref=provider.account_ref)
    assert not dispatch(case,'approve',{'action_id':action['id'],'expected_hash':action['hash'],'confirm':True})['ok']
    assert not dispatch(case,'propose',args,readonly=True)['ok'] and provider.calls==[]


def test_manifest_same_on_both_surfaces_and_max_present():
    from universal_userio import mcp_catalog, mcp_surface
    canonical={x.name:x.as_dict() for x in mcp_catalog.TOOL_SPECS if x.name.startswith('userio.messenger.')}
    legacy={x.name:x.as_dict() for x in mcp_surface.TOOL_SPECS if x.name.startswith('userio.messenger.')}
    assert canonical and canonical==legacy
    assert 'max' in mcp_catalog.CHANNEL_SCHEMA['enum']


def test_sender_token_scoped_to_explicit_account_no_legacy_or_management(case):
    from universal_userio.personal_tokens import PersonalTokens
    store,service,provider,*_=case
    tokens=PersonalTokens(store)
    token=tokens.create(store.default_user_id,'native sender fixture',scope='messenger_actions',
                        account_refs=[provider.account_ref])
    principal=store.authenticate_token(token['token'])
    assert principal.messenger_accounts==(provider.account_ref,)
    for api in (UserIOMcpSurface(store,service),UserIOToolDispatcher(store,service)):
        assert not api.dispatch('userio.accounts.list',{},principal=principal)['ok']
        assert not api.dispatch('userio.messenger.capabilities',{'account_ref':'max'},principal=principal)['ok']
        assert api.dispatch('userio.messenger.capabilities',{'account_ref':provider.account_ref},principal=principal)['operations']['reply']['allowed']
    api=UserIOMcpSurface(store,service)
    assert api.resource_manifest(principal=principal)=={'resources':[]}
    assert all(t['name'].startswith('userio.messenger.') for t in api.tool_manifest(principal=principal)['tools'])
    with pytest.raises(ValueError):tokens.create(store.default_user_id,'bad',scope='messenger_actions',account_refs=['telegram:900000101'])


def test_sender_token_http_default_deny_without_changing_parent(case):
    from universal_userio.personal_tokens import PersonalTokens
    from universal_userio.http_api import handler
    from http.server import ThreadingHTTPServer
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    import threading
    store,service,provider,*_=case
    issued=PersonalTokens(store).create(store.default_user_id,'http fixture',scope='messenger_actions',account_refs=[provider.account_ref])
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='service-test'))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    try:
        for method,path in [('GET','/v1/accounts'),('GET','/v1/personal-tokens'),('POST','/v1/personal-tokens'),
                            ('PATCH','/v1/drafts/foreign'),('DELETE','/v1/personal-tokens/parent')]:
            req=Request('http://127.0.0.1:'+str(server.server_port)+path,method=method,
                        headers={'Authorization':'Bearer '+issued['token']})
            with pytest.raises(HTTPError) as e:urlopen(req,timeout=3)
            assert e.value.code==403
    finally:server.shutdown();server.server_close();thread.join()


def test_media_binding_uses_provider_id_not_label_or_timestamp(case):
    from universal_userio.messenger import Messenger,digest
    store,service,p,cid,source=case
    media=replace(source,message_id='-10042:88',received_at=88,body='media caption',
                  attachments=({'kind':'sticker','attachment_id':'900719925474099333','size':42},))
    service.receive(media,route_id='telegram',account_ref=p.account_ref)
    api=Messenger(store,service,store.owner())
    a=api.propose(p.account_ref,cid,p.account_ref+'|-10042:77','sticker',
                  {'media_message_id':p.account_ref+'|-10042:88','attachment_index':0},'one-media-fixture-123456')
    projected,_=api._binding(store.conversation(cid),p.account_ref+'|-10042:88')
    assert a['envelope']['media_hash']==digest({'source_binding':projected['binding'],
        'attachment_id':'900719925474099333','attachment_index':0})
    projected['at']=999.123;projected['author']['name']='new display name'
    assert api._media_hash(projected,0)==a['envelope']['media_hash']


@pytest.mark.parametrize('legacy', [False, True])
def test_events_account_cursor_initializes_without_replay_and_preserves_origin(case, legacy):
    store,service,p,cid,source=case
    args={'account_ref':p.account_ref,'limit':1}
    first=dispatch(case,'events',args,legacy=legacy)
    assert first['ok'] and first['events']==[] and first['initialized']
    after=first['next_cursor']
    store.register_account(account_id='telegram:900000101',provider='telegram',display_name='private',
                           can_read=True,can_reply=True,credential_ref='fixture')
    service.receive(replace(source,message_id='-10042:78'),route_id='telegram',account_ref='telegram:900000101')
    service.receive(replace(source,message_id='-10042:79',reconciliation=True),route_id='telegram',account_ref=p.account_ref)
    service.receive(replace(source,message_id='-10042:80',direction='outgoing'),route_id='telegram',account_ref=p.account_ref)
    service.receive(replace(source,message_id='-10042:81',body='  exact wire text\n'),route_id='telegram',account_ref=p.account_ref)
    page=dispatch(case,'events',{**args,'after':after},legacy=legacy)
    assert page['ok'] and len(page['events'])==1 and not page['has_more']
    event=page['events'][0]
    assert event['message']['source']['provider_message_id']=='81'
    assert event['message']['source']['thread_id']=='70'
    assert event['message']['author']['id']=='42'
    assert event['message']['text']=='  exact wire text\n'
    assert page['next_cursor']>after and page['provider_read_acknowledged'] is False
    assert dispatch(case,'events',{**args,'after':page['next_cursor']},legacy=legacy)['events']==[]
    assert not dispatch(case,'events',{**args,'after':True},legacy=legacy)['ok']
    assert p.calls==[]


def test_telegram_ingress_preserves_raw_source_whitespace(case):
    from universal_userio.adapters import inbox_message_from_envelope
    wire={'schema':'universal.inbox.message.v1','source':'telegram','message_id':'-10042:88',
          'sender':'42','body':'  exact body\n','received_at':1700000000,'peer_id':'-10042'}
    msg=inbox_message_from_envelope(wire,received_at=1)
    assert msg.body==wire['body']
