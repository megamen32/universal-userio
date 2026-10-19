"""Focused integration: captioned MAX files, exact indexed bytes and upload.

Expected 5s; emergency 50s. Detects missing metadata and Cyrillic-header crash.
No real provider writes: all native operations use the fixture client.
"""
import base64
import hashlib
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import unquote
from urllib.request import Request, urlopen

import pytest

from test_max_web_delivery import setup
from universal_userio.http_api import handler


@pytest.fixture
def web(tmp_path):
    store, transport, service, cid = setup(tmp_path)
    attachments=[{'kind':'file','filename':'Арматура.pdf','size':12},
                 {'kind':'file','filename':'картинка.png','size':8}]
    for idx, attachment in enumerate(attachments):
        store.upsert_attachment(source='max',message_id='max-0-123',
            attachment={**attachment,'idx':idx},user_id=store.default_user_id)
    native=[{'_type':'FILE','name':'Арматура.pdf','fileId':7},
            {'_type':'FILE','name':'картинка.png','fileId':8}]
    reads=[]
    service.max_client.get_message=lambda **kw: {'id':123,'attaches':native}
    service.max_client.media_reference=lambda **kw: (reads.append(kw) or {'url':'https://file.mycdn.me/'+str(kw['attachment']['fileId'])})
    session=store.create_oauth_session(store.default_user_id)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='fixture'))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def request(path='', *, method='GET', payload=None, authenticated=True):
        req=Request(f'http://127.0.0.1:{server.server_port}'+path,method=method,
            data=json.dumps(payload).encode() if payload else None,
            headers={**({'Cookie':'userio_web_session='+session} if authenticated else {}),
                     'Content-Type':'application/json'})
        try:
            with urlopen(req) as response:return response.status,response.headers,response.read()
        except HTTPError as error:return error.code,error.headers,error.read()
    yield request, cid, reads, transport
    server.shutdown();server.server_close();thread.join();store._connection.close()


def test_captioned_max_metadata_exposes_filename_size_mime_without_downloading(web):
    request,cid,reads,_=web
    code,_,body=request(f'/v1/conversations/{cid}/media/max-0-123?attachment_index=1')
    meta=json.loads(body)
    assert code==200 and meta['available'] is True
    assert (meta['filename'],meta['size'],meta['content_type'])==('картинка.png',8,'image/png')
    assert meta['download_url'].endswith('/raw?attachment_index=1')
    assert len(meta['attachments'])==2 and meta['attachments'][0]['filename']=='Арматура.pdf'
    assert reads==[]


def test_max_raw_download_uses_exact_index_and_unicode_filename(web, monkeypatch):
    request,cid,reads,_=web
    monkeypatch.setattr('universal_userio.matrix_max_media.fetch_native_url',lambda url,**kw:(url.rsplit('/',1)[1].encode(),'application/pdf'))
    code,headers,body=request(f'/v1/conversations/{cid}/media/max-0-123/raw?attachment_index=1')
    assert code==200 and body==b'8'
    assert int(headers['Content-Length'])==len(body)
    assert headers['X-Content-SHA256']==hashlib.sha256(body).hexdigest()
    assert reads[0]['attachment']['fileId']==8
    assert "filename*=UTF-8''" in headers['Content-Disposition']
    assert unquote(headers['Content-Disposition'].split("filename*=UTF-8''")[1])=='картинка.png'


@pytest.mark.parametrize('suffix',['','/raw'])
@pytest.mark.parametrize('index',['-1','100','wrong','0&attachment_index=1'])
def test_bad_index_is_refused_before_native_read(web,suffix,index):
    request,cid,reads,_=web
    assert request(f'/v1/conversations/{cid}/media/max-0-123{suffix}?attachment_index={index}')[0]==400
    assert reads==[]


@pytest.mark.parametrize('suffix',['','/raw'])
def test_out_of_range_and_foreign_conversation_do_not_return_media(web,suffix):
    request,cid,reads,_=web
    assert request(f'/v1/conversations/{cid}/media/max-0-123{suffix}?attachment_index=2')[0]==404
    assert request(f'/v1/conversations/foreign/media/max-0-123{suffix}')[0]==404
    assert request(f'/v1/conversations/{cid}/media/max-0-123{suffix}',authenticated=False)[0]==401
    assert reads==[]


@pytest.mark.parametrize('filename,mime,data',[('проверка.pdf','application/pdf',b'%PDF-fixture'),('картинка.png','image/png',b'png-fixture')])
def test_max_web_upload_stages_original_bytes_without_provider_send(web,filename,mime,data):
    request,cid,reads,transport=web
    payload={'filename':filename,'mime_type':mime,'data_base64':base64.b64encode(data).decode(),
             'sha256':hashlib.sha256(data).hexdigest(),'request_id':'web-upload-fixture-00001'}
    first=request(f'/v1/conversations/{cid}/uploads',method='POST',payload=payload)
    replay=request(f'/v1/conversations/{cid}/uploads',method='POST',payload=payload)
    assert first[0]==replay[0]==201
    meta=json.loads(first[2]);assert meta['filename']==filename and meta['size']==len(data)
    assert meta['upload_id']==json.loads(replay[2])['upload_id']
    assert reads==[] and transport.sent==[]
