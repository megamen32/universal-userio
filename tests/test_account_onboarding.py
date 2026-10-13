"""Focused integration: one account entry, user isolation; expected3/max30s."""
from pathlib import Path
import threading,json
from http.server import ThreadingHTTPServer
from urllib.request import urlopen,Request
from urllib.error import HTTPError
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore
from universal_userio.http_api import handler


def test_unified_accounts_and_connector_owner_use_actual_personal_principal(tmp_path):
    store=SQLiteUserIOStore(tmp_path/'db');service=UserIOService(store,object(),object())
    alice,alice_token=store.create_user('alice-onboarding','test-password-123');bob,bob_token=store.create_user('bob-onboarding','test-password-123')
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='fixture-service'))
    thread=threading.Thread(target=server.serve_forever);thread.start();base='http://127.0.0.1:'+str(server.server_port)
    def read(path,token):
        try:
            with urlopen(Request(base+path,headers={'Authorization':'Bearer '+token}),timeout=5) as r:return r.status,r.read().decode()
        except HTTPError as error:return error.code,error.read().decode()
    try:
        for path in ['/accounts','/accounts/connect/new','/gmail/connect/new','/mail/connect/new','/matrix/connect/new','/vk/connect/new','/vk/personal/connect/new']:
            code,html=read(path,alice_token);assert code==200 and '<title>Аккаунты · UserIO</title>' in html
            assert 'data-open="telegram"' in html and 'data-open="yandex"' in html
        for token,user in [(alice_token,alice),(bob_token,bob)]:
            code,body=read('/v1/connection-principal',token);data=json.loads(body)
            assert code==200 and data['user_id']==user.user_id and data['can_manage'] is True
        code,body=read('/v1/connection-principal','fixture-service');assert code==200 and json.loads(body)['can_manage'] is False
        assert read('/v1/connection-principal','wrong')[0]==401
    finally:server.shutdown();thread.join();server.server_close();store.close()
