"""Authenticated UI path for one selected Telegram attachment; no provider mutation."""
import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import quote, unquote
from urllib.request import Request, urlopen

import pytest

from universal_userio.contracts import ChannelFile, InboxMessage
from universal_userio.http_api import handler
from universal_userio.service import UserIOService
from universal_userio.store import SQLiteUserIOStore


@pytest.fixture
def telegram_ui(tmp_path, monkeypatch):
    store=SQLiteUserIOStore(tmp_path/'userio.sqlite3')
    service=UserIOService(store,object(),object())
    account='telegram:900'
    store.register_account(account_id=account,provider='telegram',display_name='Nikita',
                           can_read=True,can_reply=True,credential_ref='telegram-qr:fixture')
    message_id=f'{account}|-1003724214344:281'
    conversation_id,_=service.receive(InboxMessage(
        'telegram',message_id,'Group','Вложения',1700000000,
        peer_id='-1003724214344',conversation_kind='group',attachments=(
            {'kind':'document','filename':'wrong.pdf','content_type':'application/pdf','size':3,'idx':0},
            {'kind':'document','filename':'Выбранный.xlsx',
             'content_type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             'size':9,'idx':1},
        )),route_id='telegram',account_ref=account)
    data=b'PK\x03\x04bytes'
    calls=[]

    class Adapter:
        def download(self,*,file_ref,attachment_index=0):
            calls.append((file_ref,attachment_index))
            return ChannelFile('Выбранный.xlsx',
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',data)

    monkeypatch.setattr('universal_userio.http_api._adapter_for_message',
                        lambda *_args: Adapter())
    session=store.create_oauth_session(store.default_user_id)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='fixture'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    path=f'/v1/conversations/{quote(conversation_id,safe="")}/media/{quote(message_id,safe="")}'

    def request(url,*,authenticated=True):
        req=Request(base+url,headers={'Cookie':f'userio_web_session={session}'} if authenticated else {})
        try:
            with urlopen(req) as response:return response.status,response.headers,response.read()
        except HTTPError as error:return error.code,error.headers,error.read()

    yield request,path,calls,data
    server.shutdown();server.server_close();thread.join();store.close()


def test_ui_metadata_and_raw_download_address_the_selected_telegram_attachment(telegram_ui):
    request,path,calls,data=telegram_ui
    status,_,body=request(path+'?attachment_index=1')
    meta=json.loads(body)
    assert status==200 and meta['available'] is True
    assert (meta['filename'],meta['content_type'],meta['size'])==(
        'Выбранный.xlsx','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',len(data))
    assert meta['download_url'].endswith('?attachment_index=1')
    status,headers,body=request(path+'/raw?attachment_index=1')
    assert status==200 and body==data
    assert headers['Content-Type']=='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    assert int(headers['Content-Length'])==len(data)
    assert headers['X-Content-SHA256']==hashlib.sha256(data).hexdigest()
    assert calls==[(unquote(path.rsplit('/media/',1)[1]),1)]*2


def test_ui_telegram_download_requires_owned_conversation_and_valid_index(telegram_ui):
    request,path,calls,_=telegram_ui
    assert request(path+'/raw?attachment_index=1',authenticated=False)[0]==401
    assert request('/v1/conversations/foreign/media/message/raw?attachment_index=1')[0]==404
    assert request(path+'/raw?attachment_index=-1')[0]==400
    assert calls==[]
