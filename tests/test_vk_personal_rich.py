"""Focused VK rich: real fields, stable edit IDs/media and bounded upload (10s/30s)."""
import io,json,hashlib
import pytest
from universal_userio.channels.vk_personal import VKPersonalClient

class Response(io.BytesIO):
    def __enter__(self):return self
    def __exit__(self,*a):self.close()

def test_repeat_edit_preserves_document_and_rebuilds_link_preview():
    calls=[];text=['before'];row={'id':31,'peer_id':111,'from_id':42,'out':1,'date':1760000000,
        'attachments':[{'type':'doc','doc':{'owner_id':42,'id':77,'access_key':'stable','title':'file.txt','url':'https://ps.vk.com/file'}}]}
    from urllib.parse import parse_qs
    def opener(req,timeout):
        method=req.full_url.rsplit('/',1)[-1];args=parse_qs(req.data.decode());calls.append((method,args))
        if method=='messages.getById':return Response(json.dumps({'response':{'items':[{**row,'text':text[0]}]}}).encode())
        assert method=='messages.edit';text[0]=args['message'][0]
        assert args['attachment']==['doc42_77_stable'] and args['keep_snippets']==['0']
        return Response(b'{"response":1}')
    c=VKPersonalClient('fixture-private-token',opener=opener)
    for before,after in [('before','https://example.com/a'),('https://example.com/a','plain after second edit')]:
        updated=c.edit(peer_id=111,message_id=31,body=after,expected_body=before,own_id=42)
        assert updated['id']==31 and updated['text']==after
    assert len([x for x in calls if x[0]=='messages.edit'])==2
    with pytest.raises(ValueError):c.edit(peer_id=111,message_id=31,body='bad',expected_body='old',own_id=42)
    with pytest.raises(ValueError):c.edit(peer_id=112,message_id=31,body='bad',expected_body=text[0],own_id=42)
    assert len([x for x in calls if x[0]=='messages.edit'])==2

@pytest.mark.parametrize('photo',[True,False])
def test_upload_and_send_native_attachment_reference(photo):
    calls=[];data=b'own bounded bytes';mime='image/png' if photo else 'text/plain';name='sample.png' if photo else 'sample.txt'
    def opener(req,timeout):
        calls.append(req)
        if req.full_url=='https://pu.vk.com/upload':
            assert data in req.data and name.encode() in req.data
            return Response(b'{"photo":"native","server":1,"hash":"hash"}' if photo else b'{"file":"saved-file"}')
        method=req.full_url.rsplit('/',1)[-1]
        if method.endswith('getMessagesUploadServer'):return Response(b'{"response":{"upload_url":"https://pu.vk.com/upload"}}')
        if method=='photos.saveMessagesPhoto':return Response(b'{"response":[{"owner_id":42,"id":77,"access_key":"ak"}]}')
        if method=='docs.save':return Response(b'{"response":{"type":"doc","doc":{"owner_id":42,"id":77,"access_key":"ak"}}}')
        if method=='messages.send':
            from urllib.parse import parse_qs
            args=parse_qs(req.data.decode());assert args['attachment']==[('photo' if photo else 'doc')+'42_77_ak'];assert args['random_id']==['123']
            return Response(b'{"response":31}')
        raise AssertionError(method)
    c=VKPersonalClient('fixture-private-token',opener=opener)
    ref=c.upload(peer_id=111,data=data,filename=name,mime_type=mime)
    assert ref==('photo' if photo else 'doc')+'42_77_ak'
    assert c.send(111,'caption',123,attachment=ref)=='31'
    with pytest.raises(ValueError):c.upload(peer_id=111,data=b'x'*(2*1024*1024+1),filename=name,mime_type=mime)

from test_vk_personal_messenger import app
from test_vk_personal_send import prepare,Sender
import base64


def test_normal_web_file_send_repeat_edits_and_download(app,monkeypatch):
    from universal_userio.http_api import handler
    from http.server import ThreadingHTTPServer
    from urllib.request import urlopen,Request
    from urllib.error import HTTPError
    import threading
    store,service,user,cid=prepare(app,monkeypatch)
    native={'id':333,'peer_id':111,'from_id':42,'out':1,'date':1760000100,'text':'caption',
            'attachments':[{'type':'doc','doc':{'owner_id':42,'id':77,'title':'proof.txt','url':'https://ps.vk.com/file','size':5}}]}
    calls={'upload':0,'edit':0}
    def upload(self,**kw):calls['upload']+=1;assert kw['data']==b'proof';return 'doc42_77'
    def edit(self,**kw):
        calls['edit']+=1;assert native['text']==kw['expected_body'];native['text']=kw['body'];native['update_time']=1760000200+calls['edit'];return dict(native)
    def download(self,row,index=0):return {'data':b'proof','filename':'proof.txt','content_type':'text/plain'}
    # Stub exposed only the real client seams; all HTTP/store/claims are actual.
    monkeypatch.setattr(Sender,'upload',upload,raising=False);monkeypatch.setattr(Sender,'edit',edit,raising=False)
    monkeypatch.setattr(Sender,'download',download,raising=False);monkeypatch.setattr(Sender,'message',lambda self,mid:dict(native))
    def send(self,peer,text,random_id,*,attachment=''):
        assert attachment=='doc42_77';self.sent.append((self.token,peer,text,random_id));native['text']=text;return '333'
    monkeypatch.setattr(Sender,'send',send)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(service,token='fixture-service'));thread=threading.Thread(target=server.serve_forever);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def req(method,path,body=None,token=app[3]):
        r=Request(base+path,method=method,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with urlopen(r,timeout=5) as x:
            content=x.read();return x.status, json.loads(content) if x.headers.get('Content-Type','').startswith('application/json') else content
    path='/v1/conversations/'+cid
    try:
        _,uploaded=req('POST',path+'/uploads',{'filename':'proof.txt','mime_type':'text/plain','sha256':hashlib.sha256(b'proof').hexdigest(),'data_base64':base64.b64encode(b'proof').decode(),'request_id':'vk-rich-file-request-12345'})
        payload={'body':'caption','request_id':'vk-rich-send-request-12345','upload_id':uploaded['upload_id']}
        for _ in range(2):assert req('POST',path+'/send',payload)[1]['receipt']=='333'
        assert calls['upload']==1 and len(Sender.sent)==1
        mid='vk:42|111:333';url=path+'/messages/'+mid
        for before,after in [('caption','https://example.com/new'),('https://example.com/new','second edit')]:
            _,result=req('PATCH',url,{'body':after,'expected_body':before});assert result['message']['body']==after
        assert calls['edit']==2
        _,page=req('GET',path);assert page['messages'][-1]['attachments'][0]['filename']=='proof.txt'
        _,meta=req('GET',path+'/media/'+mid);assert meta['available'] and meta['filename']=='proof.txt'
        assert req('GET',meta['download_url'])[1]==b'proof'
        row=store._connection.execute('SELECT data FROM messenger_uploads WHERE id=?',(uploaded['upload_id'],)).fetchone();assert row[0] is None
        # Native and local old-text conflicts are denied without another edit.
        with pytest.raises(HTTPError):req('PATCH',url,{'body':'wrong','expected_body':'original'})
        assert calls['edit']==2
    finally:server.shutdown();server.server_close();thread.join()


def test_existing_native_attachment_keeps_metadata_on_reconciliation(app,monkeypatch):
    store,service,user,cid=prepare(app,monkeypatch)
    from universal_userio.account_connections import vk_personal_message
    row={'id':15,'peer_id':111,'from_id':42,'out':1,'date':1760000400,'text':'',
         'attachments':[{'type':'photo','photo':{'owner_id':42,'id':77,'sizes':[{'width':10,'height':10,'url':'https://ps.vk.com/image.jpg'}]}}]}
    m=vk_personal_message(account_id='vk:42',peer_id=111,row=row,title='test',reconciled=True,peer_type='user')
    service.receive(m,route_id='vk-personal-read-only',user_id=user,account_ref='vk:42')
    actual=store.message('vk:42|111:15',source='vk',user_id=user)
    assert actual['body']=='[Вложение VK]' and store.attachments_for_message(source='vk',message_id=actual['message_id'],user_id=user)[0]['attachment_id']=='photo42_77'


def test_create_owned_vk_chat_resolves_profile_without_fake_message(app,monkeypatch):
    store,service,user,_=prepare(app,monkeypatch)
    monkeypatch.setattr(Sender,'call',lambda self,name,**args:[{'id':111,'first_name':'Иван','last_name':'Иванов'}],raising=False)
    from universal_userio.vk_web import VKWeb
    conv=VKWeb(service,user).create_conversation('vk:42','https://vk.ru/known-person')
    assert conv['account_ref']=='vk:42' and conv['peer_id']=='111'
    assert conv['id']==store.conversation_id_for_key('vk:vk:42:111',user_id=user)
    with pytest.raises(ValueError):VKWeb(service,user).create_conversation('vk:42','https://example.com/person')


def test_lost_edit_response_is_reconciled_without_second_mutation(app,monkeypatch):
    store,service,user,cid=prepare(app,monkeypatch)
    from universal_userio.account_connections import vk_personal_message
    row={'id':18,'peer_id':111,'from_id':42,'out':1,'date':1760000000,'text':'original'}
    service.receive(vk_personal_message(account_id='vk:42',peer_id=111,row=row,reconciled=True,peer_type='user'),route_id='vk-personal-read-only',account_ref='vk:42',user_id=user)
    writes=[]
    def edit(self,**kw):writes.append(kw);row['text']=kw['body'];row['update_time']=1760000200;raise RuntimeError('lost acknowledgement')
    monkeypatch.setattr(Sender,'edit',edit,raising=False);monkeypatch.setattr(Sender,'message',lambda self,mid:dict(row))
    from universal_userio.vk_web import VKWeb
    web=VKWeb(service,user)
    with pytest.raises(RuntimeError):web.edit(cid,'vk:42|111:18','new','original')
    actual=web.edit(cid,'vk:42|111:18','new','original')
    assert actual['body']=='new' and len(writes)==1


def test_file_upload_claim_is_not_replayed_after_transport_unknown(app,monkeypatch):
    store,service,user,cid=prepare(app,monkeypatch)
    from universal_userio.vk_web import VKWeb
    web=VKWeb(service,user)
    uploaded=web.stage(cid,{'filename':'own.txt','mime_type':'text/plain','data_base64':base64.b64encode(b'proof').decode(),'sha256':hashlib.sha256(b'proof').hexdigest(),'request_id':'unknown-upload-request-123'})
    calls=[]
    def upload(self,**kw):calls.append(kw);raise RuntimeError('unknown upload result')
    monkeypatch.setattr(Sender,'upload',upload,raising=False)
    for _ in range(2):
        with pytest.raises(Exception):service.send_manual_message(cid,body='',request_id='unknown-upload-send-123',user_id=user,upload_id=uploaded['upload_id'])
    assert len(calls)==1 and not Sender.sent


def test_outgoing_vk_keeps_account_author_separate_from_peer_title(app,monkeypatch):
    store,service,user,cid=prepare(app,monkeypatch)
    from universal_userio.account_connections import vk_personal_message
    m=vk_personal_message(account_id='vk:42',peer_id=111,row={'id':20,'from_id':42,'out':1,'date':1760000000,'text':'outgoing'},title='Иван собеседник',reconciled=True,peer_type='user')
    assert m.sender_name=='' and m.conversation_title=='Иван собеседник'
    service.receive(m,route_id='vk-personal-read-only',user_id=user,account_ref='vk:42')
    assert store.conversation(cid,user_id=user)['display_name']=='Иван собеседник'
