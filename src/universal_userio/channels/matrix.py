"""Matrix provider reader with no UserIO store/UI/MCP dependency."""
from __future__ import annotations
import json, os, signal, threading, time, urllib.parse, urllib.request
from dataclasses import dataclass
from typing import Any

class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Matrix redirects may not forward bearer tokens")


def _matrix_open(request, timeout):
    return urllib.request.build_opener(_RejectRedirect).open(request, timeout=timeout)


@dataclass(frozen=True, slots=True)
class MatrixMessage:
    room_id: str
    event_id: str
    sender: str
    body: str
    received_at: float = 0.0
    direction: str = "incoming"

class MatrixReader:
    def __init__(self, homeserver:str, token:str, room_ids:tuple[str,...], own_user_id:str, runner:Any=_matrix_open, *, include_own:bool=False)->None:
        self.homeserver=homeserver.rstrip('/'); self.token=token; self.room_ids=frozenset(room_ids); self.own_user_id=own_user_id; self.runner=runner; self.include_own=include_own
        if not self.homeserver or not self.token or not self.room_ids or not self.own_user_id: raise ValueError('matrix config incomplete')
    def _gap(self, room_id, start, stop, limit, budget):
        if not isinstance(start,str) or not start:
            raise RuntimeError('limited Matrix timeline has no history token')
        events=[]; seen=set()
        while start:
            if start in seen or budget['calls']>=64 or time.monotonic()>=budget['deadline']:
                raise RuntimeError('Matrix history gap exceeds catch-up budget')
            seen.add(start);budget['calls']+=1
            query={'from':start,'to':stop,'dir':'b','limit':min(100,max(1,limit)),
                   'filter':json.dumps({'types':['m.room.message']},separators=(',',':'))}
            url=self.homeserver+'/_matrix/client/v3/rooms/'+urllib.parse.quote(room_id,safe='')+'/messages?'+urllib.parse.urlencode(query)
            req=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.token})
            with self.runner(req,timeout=min(10,max(0.1,budget['deadline']-time.monotonic()))) as r:
                page=json.loads(r.read())
            chunk=page.get('chunk');end=page.get('end')
            if not isinstance(chunk,list) or (end is not None and not isinstance(end,str)):
                raise RuntimeError('invalid Matrix history page')
            events.extend(chunk)
            if len(events)>limit:raise RuntimeError('Matrix history batch exceeds limit')
            if end==stop:break
            # An empty filtered chunk can still have another page. Stop only
            # at the provider's end, preserving the exact previous sync bound.
            start=end
        return list(reversed(events))
    def poll(self,cursor:str|None,*,limit:int=100)->tuple[list[MatrixMessage],str]:
        q={'timeout':'0'}
        if cursor: q['since']=cursor
        req=urllib.request.Request(self.homeserver+'/_matrix/client/v3/sync?'+urllib.parse.urlencode(q),headers={'Authorization':'Bearer '+self.token})
        with self.runner(req,timeout=30) as r: data=json.loads(r.read())
        nxt=data.get('next_batch'); rooms=data.get('rooms',{}).get('join',{})
        if not isinstance(nxt,str) or not isinstance(rooms,dict): raise RuntimeError('invalid Matrix sync response')
        if cursor is None: return [],nxt
        out=[];budget={'calls':0,'deadline':time.monotonic()+45}
        for room_id,room in rooms.items():
            if (room_id not in self.room_ids and '*' not in self.room_ids) or not isinstance(room,dict): continue
            timeline=room.get('timeline',{})
            # The initial join snapshot can be limited to recent events; this
            # is fine for a new connection. Never silently skip later gaps.
            events=timeline.get('events',[])
            if timeline.get('limited') is True and cursor:
                events=self._gap(room_id,timeline.get('prev_batch'),cursor,limit-len(out),budget)+events
            seen=set()
            for ev in events:
                if not isinstance(ev,dict):continue
                event_id=ev.get('event_id')
                if event_id and event_id in seen:continue
                if event_id:seen.add(event_id)
                if not isinstance(ev,dict) or ev.get('type')!='m.room.message' or (ev.get('sender')==self.own_user_id and not self.include_own): continue
                timestamp=float(ev.get('origin_server_ts') or 0) / 1000
                direction="outgoing" if ev.get('sender')==self.own_user_id else "incoming"
                c=ev.get('content') or {}
                relates=c.get('m.relates_to') or {}
                if relates.get('rel_type')=='m.replace':
                    # An edit is a new event replacing an older one. Deliver it
                    # under the ORIGINAL event id so the mirror rewrites that
                    # message in place instead of appending "* edited" noise.
                    new_content=c.get('m.new_content') or {}
                    body=new_content.get('body'); original=relates.get('event_id')
                    if not (isinstance(body,str) and body and isinstance(original,str) and original):
                        continue
                    if len(out)>=limit: raise RuntimeError('Matrix batch exceeds limit')
                    out.append(MatrixMessage(room_id,original,str(ev.get('sender') or ''),body,timestamp,direction))
                    continue
                if c.get('msgtype')!='m.text' or not isinstance(c.get('body'),str): continue
                if len(out)>=limit: raise RuntimeError('Matrix batch exceeds limit')
                out.append(MatrixMessage(room_id,str(ev.get('event_id') or ''),str(ev.get('sender') or ''),c['body'],timestamp,direction))
        return [m for m in out if m.event_id and m.sender],nxt
