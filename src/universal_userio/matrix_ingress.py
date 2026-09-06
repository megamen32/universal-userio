"""Matrix read ingress for Universal UserIO."""
from __future__ import annotations
import json, os, signal, threading, urllib.parse, urllib.request
from dataclasses import dataclass
from typing import Any
from .gmail_ingress import UserIOIngressClient

@dataclass(frozen=True, slots=True)
class MatrixMessage:
    room_id: str
    event_id: str
    sender: str
    body: str

class MatrixReader:
    def __init__(self, homeserver:str, token:str, room_ids:tuple[str,...], own_user_id:str, runner:Any=urllib.request.urlopen)->None:
        self.homeserver=homeserver.rstrip('/'); self.token=token; self.room_ids=frozenset(room_ids); self.own_user_id=own_user_id; self.runner=runner
        if not self.homeserver or not self.token or not self.room_ids or not self.own_user_id: raise ValueError('matrix config incomplete')
    def poll(self,cursor:str|None,*,limit:int=100)->tuple[list[MatrixMessage],str]:
        q={'timeout':'0'}
        if cursor: q['since']=cursor
        req=urllib.request.Request(self.homeserver+'/_matrix/client/v3/sync?'+urllib.parse.urlencode(q),headers={'Authorization':'Bearer '+self.token})
        with self.runner(req,timeout=30) as r: data=json.loads(r.read())
        nxt=data.get('next_batch'); rooms=data.get('rooms',{}).get('join',{})
        if not isinstance(nxt,str) or not isinstance(rooms,dict): raise RuntimeError('invalid Matrix sync response')
        if cursor is None: return [],nxt
        out=[]
        for room_id,room in rooms.items():
            if room_id not in self.room_ids or not isinstance(room,dict): continue
            timeline=room.get('timeline',{})
            if timeline.get('limited') is True: raise RuntimeError('limited Matrix timeline')
            for ev in timeline.get('events',[]):
                if not isinstance(ev,dict) or ev.get('type')!='m.room.message' or ev.get('sender')==self.own_user_id: continue
                c=ev.get('content') or {}
                if c.get('msgtype')!='m.text' or not isinstance(c.get('body'),str): continue
                if len(out)>=limit: raise RuntimeError('Matrix batch exceeds limit')
                out.append(MatrixMessage(room_id,str(ev.get('event_id') or ''),str(ev.get('sender') or ''),c['body']))
        return [m for m in out if m.event_id and m.sender],nxt

def poll_once(sink:UserIOIngressClient,reader:MatrixReader,*,limit:int=100)->int:
    cursor=sink.cursor('matrix'); messages,nxt=reader.poll(cursor,limit=limit)
    for m in messages:
        sink.send_message(source='matrix', account_id='matrix', route_id='userio-reply', message_id=f'{m.room_id}:{m.event_id}', sender=m.sender, body=m.body)
    sink.set_cursor('matrix',nxt)
    return len(messages)

def main()->int:
    token=os.environ['USERIO_API_TOKEN']; sink=UserIOIngressClient(os.environ.get('USERIO_INGRESS_URL','http://127.0.0.1:18093'),token)
    reader=MatrixReader(os.environ['USERIO_MATRIX_HOMESERVER'],os.environ['USERIO_MATRIX_ACCESS_TOKEN'],tuple(x.strip() for x in os.environ['USERIO_MATRIX_ROOM_IDS'].split(',') if x.strip()),os.environ['USERIO_MATRIX_USER_ID'])
    interval=float(os.environ.get('USERIO_MATRIX_POLL_INTERVAL_SECONDS','2')); limit=int(os.environ.get('USERIO_MATRIX_POLL_LIMIT','100')); stop=threading.Event()
    for sig in (signal.SIGINT,signal.SIGTERM): signal.signal(sig,lambda *_:stop.set())
    while not stop.is_set():
        poll_once(sink,reader,limit=limit); stop.wait(interval)
    return 0
if __name__=='__main__': raise SystemExit(main())
