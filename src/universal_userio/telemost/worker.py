"""Bounded native RTC worker on44; private Unix API, one meeting at a time."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
from . import meeting_url
from .transport import Receiver

ROOT=Path(os.environ.get('USERIO_TELEMOST_ROOT',str(Path.home()/'.local/state/userio-telemost')))


class Worker:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.active={}

    def save(self,record):
        folder=self.root/record['id'];folder.mkdir(mode=0o700,exist_ok=True)
        temp=folder/('.'+uuid.uuid4().hex)
        with temp.open('x') as f:
            os.chmod(temp,0o600);json.dump(record,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(temp,folder/'meeting.json')

    def get(self,identity,owner):
        identity=str(uuid.UUID(identity))
        record=json.loads((self.root/identity/'meeting.json').read_text())
        if record['owner']!=owner:raise KeyError('meeting not found')
        if record['status'] in {'accepted','waiting','recording','finalizing'} and identity not in self.active:
            record['status']='unknown'
        return record

    def start(self,payload):
        owner=payload.get('owner');request_id=payload.get('request_id')
        if (not isinstance(owner,str) or not 1<=len(owner)<=128 or not isinstance(request_id,str)
            or not 16<=len(request_id)<=128 or payload.get('confirm') is not True
            or type(payload.get('max_seconds')) is not int or not 30<=payload['max_seconds']<=7200):
            raise ValueError('invalid meeting bounds')
        url=meeting_url(payload.get('url'))
        identity=str(uuid.uuid5(uuid.NAMESPACE_URL,'userio:telemost:'+owner+':'+request_id))
        if (self.root/identity/'meeting.json').exists():
            previous=self.get(identity,owner)
            if (previous['url'],previous['max_seconds'])!=(url,payload['max_seconds']):
                raise ValueError('request ID belongs to another meeting')
            return previous
        if self.active:raise RuntimeError('recorder is busy')
        if sum(x.stat().st_size for x in self.root.glob('*/*') if x.is_file())>8*1024**3:
            raise RuntimeError('recording archive is full')
        if os.statvfs(self.root).f_bavail*os.statvfs(self.root).f_frsize<22*1024**3:
            raise RuntimeError('disk reserve unavailable')
        record={'id':identity,'owner':owner,'url':url,'request_id':request_id,
                'max_seconds':payload['max_seconds'],'status':'accepted','started_at':time.time(),
                'tracks':[],'participants':[],'partial':False,'chat_supported':False,
                'mic_enabled':False,'camera_enabled':False}
        self.save(record)
        stop=asyncio.Event();receiver=Receiver(record,self.root/identity,lambda:self.save(record),stop)
        task=asyncio.create_task(self.run(receiver));self.active[identity]=(stop,task)
        return record.copy()

    async def run(self,receiver):
        try:await receiver.run()
        except Exception as error:
            receiver.record.update(status='failed',error=str(error) if str(error).startswith('telemost_http_') else type(error).__name__,ended_at=time.time())
        finally:
            await receiver.close()
            for task in receiver.tasks:task.cancel()
            await asyncio.gather(*receiver.tasks,return_exceptions=True)
            self.save(receiver.record);self.active.pop(receiver.record['id'],None)

    def stop(self,identity,owner):
        record=self.get(identity,owner)
        if identity in self.active:self.active[identity][0].set()
        return record


async def main():
    from aiohttp import web
    worker=Worker();socket_path=ROOT/'worker.sock'
    if socket_path.exists():socket_path.unlink()
    async def execute(request):
        try:
            payload=await request.json();op=payload.get('op')
            if op=='start':record=worker.start(payload)
            elif op in {'get','stop'}:record=getattr(worker,op)(payload['id'],payload['owner'])
            else:raise ValueError('invalid operation')
            return web.json_response(record)
        except (ValueError,KeyError,RuntimeError,FileNotFoundError) as error:
            return web.json_response({'error':type(error).__name__},status=409)
    async def audio(request):
        record=worker.get(request.match_info['identity'],request.query.get('owner',''))
        index=int(request.match_info['index'])
        track=next(t for t in record['tracks'] if t['index']==index)
        if record['status'] not in {'recorded','failed','no_audio'}:raise web.HTTPConflict()
        return web.FileResponse(worker.root/record['id']/track['filename'])
    async def pending(request):
        """Tail of a still growing track: raw PCM after a byte offset, no WAV header.

        A live transcript must not wait for the meeting to end. The recorder
        appends mono s16 16 kHz frames to the same file, so bytes already read
        are simply never sent again. Owner scope is identical to /audio.
        """
        record=worker.get(request.match_info['identity'],request.query.get('owner',''))
        index=int(request.match_info['index'])
        track=next((t for t in record['tracks'] if t['index']==index),None)
        if track is None or record['status'] not in {'accepted','waiting','recording','finalizing','recorded','failed'}:
            raise web.HTTPConflict()
        offset=int(request.query.get('offset','0'))
        if offset<0:raise web.HTTPBadRequest()
        path=worker.root/record['id']/track['filename']
        # Header is rewritten only on close, so a live file is header + raw PCM.
        size=path.stat().st_size if path.exists() else 44
        start=44+offset
        if start>=size:return web.Response(body=b'',content_type='application/octet-stream')
        with path.open('rb') as stream:
            stream.seek(start);payload=stream.read(size-start)
        # Only whole frames; a half frame would shift every later offset.
        return web.Response(body=payload[:len(payload)-(len(payload)%2)],content_type='application/octet-stream')
    app=web.Application(client_max_size=8192);app.router.add_post('/execute',execute)
    app.router.add_get('/audio/{identity}/{index}',audio)
    app.router.add_get('/pending/{identity}/{index}',pending)
    runner=web.AppRunner(app,access_log=None);await runner.setup()
    site=web.UnixSite(runner,str(socket_path));await site.start();os.chmod(socket_path,0o600)
    try:await asyncio.Event().wait()
    finally:
        for stop,task in worker.active.values():stop.set()
        await asyncio.gather(*(task for stop,task in worker.active.values()),return_exceptions=True)
        await runner.cleanup();socket_path.unlink(missing_ok=True)


if __name__=='__main__':asyncio.run(main())
