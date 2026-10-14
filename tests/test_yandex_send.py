"""Focused integration: exact destination, tenant and uncertain-send no replay.

Expected2s/max20s. Provider is mocked; real delivery is a separate web canary.
"""
import pytest
from universal_userio.store import SQLiteUserIOStore
from universal_userio.service import UserIOService
from universal_userio.account_connections import ConnectorSecrets
from universal_userio.yandex.web import YandexWeb

@pytest.fixture
def setup(tmp_path,monkeypatch):
    root=tmp_path/'secrets';monkeypatch.setenv('USERIO_CONNECTION_SECRETS_DIR',str(root))
    store=SQLiteUserIOStore(tmp_path/'test.db');service=UserIOService(store,None,None)
    own='11111111-1111-1111-1111-111111111111'
    target='22222222-2222-2222-2222-222222222222'
    account='yandex:100000001'
    ref=ConnectorSecrets().save({'provider':'yandex','user_id':store.default_user_id,
        'account_id':account,'uid':'100000001','guid':own,'cookies':{'test':'fake'}})
    store.register_account(account_id=account,provider='yandex',display_name='Test',
        can_read=True,can_reply=True,credential_ref=ref)
    yield service,own,target,account
    store.close()

def test_link_resolves_exact_account_target_and_no_fake_messages(setup,monkeypatch):
    service,own,target,account=setup
    calls=[]
    def read(secret,**kwargs):
        calls.append(kwargs)
        return {'peer_id':own+'_'+target,'person':{'guid':target,'display_name':'Test recipient'}}
    monkeypatch.setattr('universal_userio.yandex.web.read',read)
    result=YandexWeb(service,service._store.default_user_id).create_conversation(account,
        'https://telemost.yandex.ru/user/'+target)
    assert result['peer_id']==own+'_'+target
    assert result['display_name']=='Test recipient' and result['messages']==[]
    assert calls==[{'operation':'resolve','target_guid':target}]
    with pytest.raises(Exception):YandexWeb(service,'other-owner').create_conversation(account,target)

def test_uncertain_send_keeps_claim_and_never_resends(setup,monkeypatch):
    service,own,target,account=setup;calls=[]
    def read(secret,**kwargs):
        if kwargs['operation']=='resolve':
            return {'peer_id':own+'_'+target,'person':{'guid':target,'display_name':'Test'}}
        calls.append(kwargs);raise TimeoutError('simulated provider timeout')
    monkeypatch.setattr('universal_userio.yandex.web.read',read)
    conv=YandexWeb(service,service._store.default_user_id).create_conversation(account,target)
    request='yandex-test-request-0001'
    with pytest.raises(TimeoutError):service.send_manual_message(conv['id'],body='Test',request_id=request)
    with pytest.raises(Exception):service.send_manual_message(conv['id'],body='Test',request_id=request)
    assert len(calls)==1 and calls[0]['chat_id']==own+'_'+target
    assert calls[0]['text']=='Test' and calls[0]['operation']=='send'
