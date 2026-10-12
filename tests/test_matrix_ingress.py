from pathlib import Path
import json
import urllib.parse
import pytest

from universal_userio.channels.matrix import MatrixReader
from universal_userio.matrix_ingress import MatrixMessage,poll_once
class Reader:
 def poll(self,cursor,*,limit): assert cursor=='c1'; return [MatrixMessage('!r','$e','@a','hi')],'c2'
class Sink:
 def __init__(self): self.c={'matrix':'c1'}; self.sent=[]
 def cursor(self,s): return self.c.get(s)
 def set_cursor(self,s,c): self.c[s]=c
 def send_message(self,**kw): self.sent.append((kw["source"],kw["message_id"],kw["sender"],kw["body"]))
def test_matrix_ingress_uses_userio_cursor_and_ingress():
 s=Sink(); assert poll_once(s,Reader())==1; assert s.c['matrix']=='c2'; assert s.sent==[('matrix','!r:$e','@a','hi')]


class _SyncResponse:
 def __init__(self,payload): self._payload=payload
 def __enter__(self): return self
 def __exit__(self,*args): return False
 def read(self): return self._payload


def _reader_with_events(events):
 payload=json.dumps({"next_batch":"c2","rooms":{"join":{"!r:hs":{"timeline":{"events":events}}}}}).encode()
 return MatrixReader("https://hs","tok",("!r:hs",),"@me:hs",runner=lambda req,timeout: _SyncResponse(payload))


def test_matrix_reader_delivers_replace_edit_under_original_event_id():
 messages,nxt=_reader_with_events([
  {"type":"m.room.message","sender":"@bot:hs","event_id":"$orig",
   "content":{"msgtype":"m.text","body":"Начинаю проверку..."}},
  {"type":"m.room.message","sender":"@bot:hs","event_id":"$edit1",
   "content":{"msgtype":"m.text","body":"* Найдено в топе: 3",
              "m.new_content":{"msgtype":"m.text","body":"Найдено в топе: 3"},
              "m.relates_to":{"rel_type":"m.replace","event_id":"$orig"}}},
 ]).poll("c1",limit=100)
 assert nxt=="c2"
 assert [(m.event_id,m.body) for m in messages]==[("$orig","Начинаю проверку..."),("$orig","Найдено в топе: 3")]


def test_matrix_reader_skips_malformed_replace_events():
 messages,_=_reader_with_events([
  {"type":"m.room.message","sender":"@bot:hs","event_id":"$bad",
   "content":{"msgtype":"m.text","body":"* пустая правка",
              "m.relates_to":{"rel_type":"m.replace","event_id":"$missing"}}},
  {"type":"m.room.message","sender":"@bot:hs","event_id":"$ok",
   "content":{"msgtype":"m.text","body":"обычное сообщение"}},
 ]).poll("c1",limit=100)
 assert [(m.event_id,m.body) for m in messages]==[("$ok","обычное сообщение")]


def test_matrix_ingress_follows_main_service_lifecycle():
 unit = (Path(__file__).parents[1] / "deploy/userio-matrix-ingress.service").read_text()
 assert "PartOf=universal-userio.service" in unit
 assert "WantedBy=universal-userio.service" in unit
 assert "Restart=always" in unit


def _event(number):
 return {'type':'m.room.message','sender':'@a:hs','event_id':f'$e{number}',
         'content':{'msgtype':'m.text','body':str(number)}}


def test_limited_sync_fills_exact_gap_before_returning_new_cursor():
 requests=[]
 def runner(req,timeout):
  requests.append(req.full_url)
  if '/sync?' in req.full_url:
   data={'next_batch':'c2','rooms':{'join':{'!r:hs':{'timeline':{
       'limited':True,'prev_batch':'p3','events':[_event(3)]}}}}}
  else:
   query=urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
   assert query['to']==['c1'] and query['dir']==['b']
   data=({'chunk':[_event(2)],'end':'p2'} if query['from']==['p3']
         else {'chunk':[_event(1)]})
  return _SyncResponse(json.dumps(data).encode())
 reader=MatrixReader('https://hs','tok',('!r:hs',),'@me:hs',runner=runner)
 messages,nxt=reader.poll('c1')
 assert [m.event_id for m in messages]==['$e1','$e2','$e3'] and nxt=='c2'
 assert len(requests)==3


def test_empty_filtered_history_page_with_end_is_not_end_of_gap():
 count=0
 def runner(req,timeout):
  nonlocal count
  count+=1
  if count==1:data={'next_batch':'c2','rooms':{'join':{'!r:hs':{'timeline':{'limited':True,'prev_batch':'p3','events':[]}}}}}
  elif count==2:data={'chunk':[],'end':'p2'}
  else:data={'chunk':[_event(1)]}
  return _SyncResponse(json.dumps(data).encode())
 result,nxt=MatrixReader('https://hs','tok',('*',),'@me:hs',runner=runner).poll('c1')
 assert len(result)==1 and nxt=='c2' and count==3


def test_unresolved_gap_refuses_new_cursor_instead_of_retry_loop():
 def runner(req,timeout):
  data=({'next_batch':'c2','rooms':{'join':{'!r:hs':{'timeline':{'limited':True,'prev_batch':'p3','events':[]}}}}}
        if '/sync?' in req.full_url else {'chunk':[],'end':'p3'})
  return _SyncResponse(json.dumps(data).encode())
 with pytest.raises(RuntimeError):
  MatrixReader('https://hs','tok',('*',),'@me:hs',runner=runner).poll('c1')


def test_exact_previous_sync_end_finishes_without_from_equals_to_request():
 requests=[]
 def runner(req,timeout):
  requests.append(req.full_url)
  data=({'next_batch':'c2','rooms':{'join':{'!r:hs':{'timeline':{'limited':True,'prev_batch':'p3','events':[_event(3)]}}}}}
        if len(requests)==1 else {'chunk':[_event(2)],'end':'c1'})
  assert len(requests)<=2
  return _SyncResponse(json.dumps(data).encode())
 messages,nxt=MatrixReader('https://hs','tok',('*',),'@me:hs',runner=runner).poll('c1')
 assert [m.event_id for m in messages]==['$e2','$e3'] and nxt=='c2'
 assert len(requests)==2
