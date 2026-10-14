import asyncio
import json
from pathlib import Path
import pytest
from universal_userio.telemost import meeting_url
from universal_userio.telemost.transport import hello,safe_media_url
from universal_userio.telemost.worker import Worker

@pytest.mark.parametrize('url',['http://telemost.yandex.ru/j/12345678901234','https://evil.invalid/j/12345678901234','https://telemost.yandex.ru@evil.invalid/j/12345678901234','https://telemost.yandex.ru/j/12345678901234?token=x'])
def test_invalid_links(url):
    with pytest.raises(ValueError):meeting_url(url)

def test_quiet_contract():
    value=hello('peer','room','test-secret','service')['hello']
    assert value['disablePublisher'] is False and value['sendAudio'] is False and value['sendVideo'] is False
    assert value['participantMeta']['sendAudio'] is False and value['participantMeta']['sendVideo'] is False
    assert 'запись' in value['participantMeta']['name']

def test_media_endpoint():
    with pytest.raises(ValueError):safe_media_url('ws://example.invalid/')
    with pytest.raises(ValueError):safe_media_url('wss://yandex.ru.evil.invalid/')

def test_durable_unknown_never_rejoins(tmp_path):
    worker=Worker(tmp_path)
    record={'id':'00000000-0000-0000-0000-000000000001','owner':'test-user','status':'recording'}
    worker.save(record)
    assert worker.get(record['id'],'test-user')['status']=='unknown'
    with pytest.raises(KeyError):worker.get(record['id'],'other-user')

def test_same_id_has_same_request_binding(tmp_path):
    import uuid
    worker=Worker(tmp_path);request='test-request-0001';owner='test-user'
    identity=str(uuid.uuid5(uuid.NAMESPACE_URL,'userio:telemost:'+owner+':'+request))
    url='https://telemost.yandex.ru/j/12345678901234'
    worker.save({'id':identity,'owner':owner,'status':'recorded','url':url,'max_seconds':120})
    payload=dict(owner=owner,request_id=request,url=url,max_seconds=120,confirm=True)
    assert worker.start(payload)['id']==identity
    with pytest.raises(ValueError):worker.start(dict(payload,max_seconds=121))

def test_unknown_submission_is_not_replayed(tmp_path,monkeypatch):
    from universal_userio.telemost.meetings import Meetings
    meetings=Meetings(None,tmp_path);calls=[]
    monkeypatch.setattr(meetings,'_command',lambda:[])
    def fail(payload):calls.append(payload);raise RuntimeError('transport lost')
    monkeypatch.setattr(meetings,'_remote',fail)
    arguments=dict(user_id='test-user',url='https://telemost.yandex.ru/j/12345678901234',request_id='test-request-0001',confirm=True,max_seconds=120)
    with pytest.raises(RuntimeError):meetings.start(**arguments)
    assert meetings.start(**arguments)['status']=='unknown'
    assert len(calls)==1

def test_remote_audio_belongs_to_owner(tmp_path):
    from universal_userio.telemost.meetings import Meetings
    meetings=Meetings(None,tmp_path)
    record={'id':'00000000-0000-0000-0000-000000000001','owner':'test-user','tracks':[
        {'index':1,'filename':'track-1.wav','archived':True}]}
    meetings._save(record)
    with pytest.raises(KeyError):meetings.audio(record['id'],'other-user',1)

def test_catalog_readonly_contract():
    from universal_userio.mcp_catalog import TOOL_SPECS,TOOL_CAPABILITIES
    from universal_userio.token_permissions import READ_ONLY_TOOLS,read_only_http_allowed
    specs={t.name:t for t in TOOL_SPECS}
    assert specs['userio.meetings.telemost.start'].input_schema['additionalProperties'] is False
    assert 'userio.meetings.telemost.get' in READ_ONLY_TOOLS
    assert 'userio.meetings.telemost.start' not in READ_ONLY_TOOLS
    assert TOOL_CAPABILITIES['userio.meetings.telemost.start']=='send'
    assert not read_only_http_allowed('POST','/v1/meetings/telemost')
