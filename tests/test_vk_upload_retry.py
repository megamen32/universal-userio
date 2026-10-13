"""Fast unit: definite VK CDN refusal, expected1s/max30s; no repeated message send."""
import io,json,urllib.error
from unittest.mock import patch
import pytest
from universal_userio.channels.vk_personal import VKPersonalClient,VKUploadRefused
class Response:
 def __init__(self,body):self.body=json.dumps(body).encode()
 def __enter__(self):return self
 def __exit__(self,*args):pass
 def read(self,size):return self.body
def fixture(errors):
 calls=[];uploads=[]
 def open_request(req,timeout):
  uploads.append(req)
  if len(uploads)<=len(errors):raise errors[len(uploads)-1]
  return Response({'file':'native temporary descriptor'})
 client=VKPersonalClient('unit-access-token',opener=open_request)
 def request(method,args,**kwargs):
  calls.append(method)
  if method=='docs.getMessagesUploadServer':return {'upload_url':'https://upload.vk.com/native'}
  if method=='docs.save':return {'doc':{'owner_id':7,'id':9}}
  if method=='messages.send':return 99
  raise AssertionError(method)
 client._request=request
 return client,calls,uploads
def refusal(code):return urllib.error.HTTPError('https://upload.vk.com/native',code,'refused',{},io.BytesIO(b''))
def test_definite_405_refreshes_upload_slot_and_sends_exactly_once():
 client,calls,uploads=fixture([refusal(405)])
 with patch('universal_userio.channels.vk_personal.time.sleep'):
  ref=client.upload(peer_id=42,data=b'actual bytes',filename='file.txt',mime_type='text/plain')
  receipt=client.send(42,'',123,attachment=ref)
 assert receipt=='99' and ref=='doc7_9'
 assert calls==['docs.getMessagesUploadServer','docs.getMessagesUploadServer','docs.save','messages.send']
 assert len(uploads)==2 and all(b'actual bytes' in req.data for req in uploads)
def test_two_405_refusals_are_bounded_and_never_send():
 client,calls,uploads=fixture([refusal(405),refusal(405)])
 with patch('universal_userio.channels.vk_personal.time.sleep'),pytest.raises(VKUploadRefused):
  client.upload(peer_id=42,data=b'actual bytes',filename='file.txt',mime_type='text/plain')
 assert len(uploads)==2 and 'messages.send' not in calls and 'docs.save' not in calls
def test_timeout_is_not_silently_retried():
 client,calls,uploads=fixture([TimeoutError()])
 with pytest.raises(RuntimeError):
  client.upload(peer_id=42,data=b'actual bytes',filename='file.txt',mime_type='text/plain')
 assert len(uploads)==1 and 'messages.send' not in calls
def test_other_http_refusals_do_not_reallocate():
 client,calls,uploads=fixture([refusal(403)])
 with pytest.raises(VKUploadRefused):
  client.upload(peer_id=42,data=b'actual bytes',filename='file.txt',mime_type='text/plain')
 assert len(uploads)==1 and 'messages.send' not in calls

