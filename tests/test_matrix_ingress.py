from pathlib import Path
import json

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
