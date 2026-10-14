"""Fast unit<1s/max10s: durable caller-bound admission and private call audio."""
import json
from unittest.mock import Mock
import wave
import pytest

from universal_userio import telegram_calls, call_audio


def test_recoverable_asr_warning_keeps_the_live_call_visible_for_restart_gates():
    # Actual62d80 kept answering after this warning. Hiding it as failed
    # incorrectly allows an API restart during a real call.
    record = {'status':'active', 'answered':True}
    telegram_calls.record_call_error(record, {'recoverable':True, 'reason':'Речь сохранена для проверки.'})
    assert record['status']=='active' and 'warning' in record and 'error' not in record
    telegram_calls.record_call_error(record, {'reason':'voice_transport_error'})
    assert record['status']=='failed'
    unknown={'status':'unknown'}
    telegram_calls.record_call_error(unknown, {'reason':'voice_transport_error'})
    assert unknown['status']=='unknown'


def test_worker_counter_uses_existing_relay_durable_request_id_contract():
    import re
    request=telegram_calls.relay_request_id('1')
    assert request=='rpc-00000001'
    assert re.fullmatch(r'[A-Za-z0-9_-]{8,128}',request)
    assert request==telegram_calls.relay_request_id('1')
    for invalid in ('0','-1','bad-counter',None):
        with pytest.raises(ValueError): telegram_calls.relay_request_id(invalid)


def service_fixture():
    service=Mock(); service._store.default_user_id='owner'
    service._store.capability_enabled.return_value=True
    service._store.accounts.return_value=[{'id':'telegram:100001','provider':'telegram','enabled':True}]
    provider=Mock(account_ref='telegram:100001',authorized_user='owner',sender_id='100001')
    service.messenger_providers={'telegram:100001':provider}
    return service,provider


def test_admitted_telegram_call_is_never_replayed_after_restart(tmp_path,monkeypatch):
    service,provider=service_fixture()
    monkeypatch.setattr(telegram_calls.threading.Thread,'start',lambda _:None)
    args=dict(user_id='owner',account_ref='telegram:100001',target='100002',
              purpose='Проверить голос',request_id='one-unique-request',confirm=True)
    first=telegram_calls.TelegramCalls(service,tmp_path).start(**args)
    after=telegram_calls.TelegramCalls(service,tmp_path)
    assert after.start(**args)==dict(first,status='unknown')
    provider._capabilities.assert_called_once(); provider._request.assert_not_called()
    assert not after.active
    with pytest.raises(KeyError): after.get(first['call_id'],'different-user')
    with pytest.raises(ValueError,match='another call'): after.start(**dict(args,purpose='Изменённое поручение'))
    with pytest.raises(ValueError,match='confirmation'): after.start(**dict(args,confirm=False))


def test_only_existing_owner_and_exact_relay_sender_can_start(tmp_path,monkeypatch):
    service,provider=service_fixture()
    monkeypatch.setattr(telegram_calls.threading.Thread,'start',lambda _:None)
    args=dict(user_id='owner',account_ref='telegram:100001',target='100002',
              purpose='Проверить голос',request_id='one-unique-request',confirm=True)
    manager=telegram_calls.TelegramCalls(service,tmp_path)
    with pytest.raises(PermissionError): manager.start(**dict(args,user_id='other'))
    provider.sender_id='100003'
    with pytest.raises(ValueError,match='identity'): manager.start(**args)
    assert not list(tmp_path.iterdir()); provider._request.assert_not_called()


def test_poll_loss_preserves_final_rpc_channel_and_unknown_admission(tmp_path,monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock
    async def check():
        reader=asyncio.StreamReader(); gate=threading.Event(); order=[]
        class Provider:
            def _request(self,method,path,payload=None):
                if path.endswith('/start'): return {}
                if '/updates?' in path: raise OSError('lost update page')
                if path.endswith('/rpc'):
                    assert payload['request_id']=='rpc-00000001'
                    order.append('rpc-enter'); assert gate.wait(1)
                    raise OSError('lost request receipt')
                if path.endswith('/status'):
                    return {'state':'CLAIMED','provider_attempted':True,'receipt':None}
                if path.endswith('/close'): pytest.fail('unknown admission must remain open')
                pytest.fail('unexpected relay route')
        class Writer:
            def write(self,line):
                frame=json.loads(line)
                if frame.get('action')=='telegram_transport':
                    reader.feed_data(json.dumps({'event':'telegram_rpc','request_id':'1',
                        'method':'phone.requestCall','params':{}}).encode()+b'\n')
                elif frame.get('type')=='hangup': order.append('hangup'); gate.set()
                elif frame.get('type')=='rpc_result':
                    order.append('final-rpc-reply'); reader.feed_eof()
            async def drain(self): pass
            def close(self): order.append('socket-close')
            async def wait_closed(self): pass
        monkeypatch.setattr(telegram_calls.asyncio,'open_unix_connection',AsyncMock(return_value=(reader,Writer())))
        service,_=service_fixture(); manager=telegram_calls.TelegramCalls(service,tmp_path)
        record={'call_id':'22222222-3333-4444-8555-666666666666','admission_id':'test-admission',
            'owner_user_id':'owner','account_ref':'telegram:100001','target':'100002',
            'purpose':'Тест','greeting':'','max_seconds':30,'status':'accepted','answered':False}
        with pytest.raises(OSError): await manager._call(record,Provider(),threading.Event())
        assert order.index('hangup')<order.index('final-rpc-reply')<order.index('socket-close')
        assert record['status']=='unknown' and record['relay_reconciliation']=='pending'
    asyncio.run(check())


def test_hangup_rejects_late_dial_but_allows_teardown_rpc(tmp_path,monkeypatch):
    import asyncio
    import threading
    from unittest.mock import AsyncMock
    async def check():
        reader=asyncio.StreamReader(); invoked=[]; replies=[]
        class Provider:
            def _request(self,method,path,payload=None):
                if path.endswith('/start'): return {}
                if '/updates?' in path: raise OSError('lost update page')
                if path.endswith('/rpc'):
                    invoked.append(payload['method']); return {'result':True}
                if path.endswith('/status'): return {'state':'PREPARED','provider_attempted':False}
                if path.endswith('/close'): return {}
                pytest.fail('unexpected relay route')
        class Writer:
            def write(self,line):
                frame=json.loads(line)
                if frame.get('type')=='hangup':
                    for counter,method in (('1','phone.requestCall'),('2','phone.discardCall')):
                        reader.feed_data(json.dumps({'event':'telegram_rpc','request_id':counter,
                            'method':method,'params':{}}).encode()+b'\n')
                elif frame.get('type')=='rpc_result':
                    replies.append(frame)
                    if frame['request_id']=='2': reader.feed_eof()
            async def drain(self): pass
            def close(self): pass
            async def wait_closed(self): pass
        monkeypatch.setattr(telegram_calls.asyncio,'open_unix_connection',AsyncMock(return_value=(reader,Writer())))
        service,_=service_fixture(); manager=telegram_calls.TelegramCalls(service,tmp_path)
        record={'call_id':'22222222-3333-4444-8555-666666666666','admission_id':'test-admission',
            'owner_user_id':'owner','account_ref':'telegram:100001','target':'100002',
            'purpose':'Тест','greeting':'','max_seconds':30,'status':'accepted','answered':False}
        with pytest.raises(OSError): await manager._call(record,Provider(),threading.Event())
        assert invoked==['phone.discardCall']
        assert replies[0]['error'] is True and replies[1]['result'] is True
        assert record['status']=='failed'
    asyncio.run(check())


def test_handshake_rpc_does_not_block_other_signaling_or_connected_event(tmp_path, monkeypatch):
    """Focused integration1s/max10s: second packet must pass a slow first RPC."""
    import asyncio
    import threading
    from unittest.mock import AsyncMock
    async def check():
        reader=asyncio.StreamReader(); second=threading.Event(); replies=[]
        class Provider:
            def _request(self, method, path, payload=None):
                if path.endswith('/start'): return {}
                if '/updates?' in path: return {'events':[], 'next_cursor':0}
                if path.endswith('/rpc'):
                    if payload['request_id']=='rpc-00000001':
                        assert second.wait(1), 'first RPC blocked the second packet'
                    else: second.set()
                    return {'result':True}
                if path.endswith('/status'): return {'state':'ACCEPTED', 'provider_attempted':True}
                if path.endswith('/close'): return {}
                pytest.fail('unexpected route')
        class Writer:
            def write(self, line):
                frame=json.loads(line)
                if frame.get('action')=='telegram_transport':
                    for counter in ('1','2'):
                        reader.feed_data(json.dumps({'event':'telegram_rpc','request_id':counter,
                            'method':'phone.sendSignalingData','params':{}}).encode()+b'\n')
                    reader.feed_data(b'{"event":"connected"}\n')
                elif frame.get('type')=='rpc_result':
                    replies.append(frame)
                    if len(replies)==2:
                        reader.feed_data(b'{"event":"ended"}\n'); reader.feed_eof()
            async def drain(self): pass
            def close(self): pass
            async def wait_closed(self): pass
        monkeypatch.setattr(telegram_calls.asyncio,'open_unix_connection',AsyncMock(return_value=(reader,Writer())))
        service,_=service_fixture(); manager=telegram_calls.TelegramCalls(service,tmp_path)
        record={'call_id':'22222222-3333-4444-8555-666666666666','admission_id':'test-admission',
            'owner_user_id':'owner','account_ref':'telegram:100001','target':'100002',
            'purpose':'Тест','greeting':'','max_seconds':30,'status':'accepted','answered':False}
        await manager._call(record, Provider(), threading.Event())
        assert record['answered'] and record['status']=='completed'
        assert len(replies)==2 and all(reply.get('result') is True for reply in replies)
    asyncio.run(check())


def test_secretary_scoped_tool_cannot_start_read_or_stop_another_account(monkeypatch):
    from dataclasses import replace
    from universal_userio.contracts import UserPrincipal
    from universal_userio.mcp_surface import UserIOMcpSurface
    from universal_userio.mcp_dispatch import UserIOToolDispatcher
    service,_=service_fixture(); manager=Mock()
    manager.get.return_value={'account_ref':'telegram:100003'}
    manager.start.return_value={'account_ref':'telegram:100001','call_id':'owned'}
    monkeypatch.setattr(telegram_calls,'calls_for',lambda _:manager)
    principal=UserPrincipal('owner','owner','owner',messenger_accounts=('telegram:100001',))
    for api in (UserIOMcpSurface(service._store,service),UserIOToolDispatcher(service._store,service)):
        for operation,args in (('start',{'account_ref':'telegram:100003'}),
                               ('get',{'call_id':'foreign'}),('stop',{'call_id':'foreign'})):
            result=api.dispatch('userio.calls.telegram.'+operation,args,principal=principal)
            assert result=={'ok':False,'error':'account_scope_denied'}
        manager.start.assert_not_called(); manager.stop.assert_not_called()
        assert api.dispatch('userio.calls.telegram.start',{'account_ref':'telegram:100001'},
                            principal=replace(principal,read_only=True))['error']=='read_only_token'
        assert api.dispatch('userio.calls.telegram.start',{'account_ref':'telegram:100001'},
                            principal=principal)['ok'] is True
        manager.start.reset_mock()
    surface=UserIOMcpSurface(service._store,service)
    names=lambda p:{t['name'] for t in surface.tool_manifest(principal=p)['tools'] if '.calls.' in t['name']}
    assert names(principal)=={'userio.calls.telegram.start','userio.calls.telegram.get','userio.calls.telegram.stop'}
    assert names(replace(principal,read_only=True))=={'userio.calls.telegram.get'}
    assert names(replace(principal,messenger_accounts=('max',)))==set()
    assert surface.dispatch('userio.calls.matrix.start',{},principal=principal)['error']=='messenger_scope_only'


def test_telegram_two_track_audio_is_bound_to_secretary_and_recipient(tmp_path):
    call_id='22222222-3333-4444-8555-666666666666'; folder=tmp_path/call_id; folder.mkdir()
    for track in ('caller','assistant'):
        with wave.open(str(folder/(track+'.wav')),'wb') as audio:
            audio.setparams((1,2,48000,0,'NONE','')); audio.writeframes(b'\x01\x00'*4800)
    (folder/'manifest.json').write_text(json.dumps({'callId':call_id,'complete':True,'sampleRate':48000,
        'peerFields':{'accountRef':'telegram:100001','peerRef':'100002'}}))
    (folder/'live-transcript.json').write_text(json.dumps({'caller':'Да, слышу','assistant':'Здравствуйте'}))
    conv={'source':'telegram','id':'conv','account_ref':'telegram:100001','peer_id':'100002'}
    msg={'source':'telegram','conversation_id':'conv','sender':call_id,'message_id':'end'}
    result=call_audio.recording(conv,msg,tmp_path)
    assert result['complete'] and result['duration']==.1
    assert [t['transcript'] for t in result['tracks']]==['Да, слышу','Здравствуйте']
    assert result['tracks'][1]['label']=='ИИ'
    assert call_audio.recording(dict(conv,account_ref='telegram:100003'),msg,tmp_path) is None
    assert call_audio.recording(dict(conv,peer_id='100004'),msg,tmp_path) is None
