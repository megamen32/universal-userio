"""Matrix provider reader with no UserIO store/UI/MCP dependency."""
from __future__ import annotations
import json, os, signal, threading, urllib.parse, urllib.request
from dataclasses import dataclass
from typing import Any

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
