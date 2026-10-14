"""MatrixRTC transport for the EXISTING AgentCall conversation engine.

Only signalling and AudioSocket/LiveKit framing live here. Speech, voice,
speed, interruption, reasoning, hangup and recordings belong to AgentCall.
Configuration is inherited from the running gateway by the authenticated
UserIO owner; credentials arrive on stdin, never process arguments or logs.
"""
from __future__ import annotations
import asyncio
from dataclasses import replace
import gc
import json
import os
from pathlib import Path
import signal
import sys
import time
from urllib.parse import quote
import uuid

from .matrix_calls import MEMBER, RING, fresh_answer, request_json


def bind_prepared(entry, record):
    return replace(entry, account_ref=record['history_account_ref'],
                   peer_ref=record['room_id'], peer_label=record['target'])


def server_voice_config(config):
    # Matrix never needs a device. Reuse AgentCall's deployed server speech
    # endpoint when its telephone profile selects Android synthesis.
    if config.tts_provider == 'android-phone':
        return replace(config, tts_provider='qwen-warm', tts_engine='silero',
            tts_url=os.environ.get('AGENTCALL_TTS_SILERO_URL','http://127.0.0.1:30802/synthesize'))
    return config


async def finish_rtc_audio(source, should_stop=None, *, interrupted=False):
    """Flush the bounded RTC tail, observing interruptions during playback."""
    # A rolling deployment can launch the new worker from a gateway that
    # still supplies the original boolean argument. Keep that seam compatible.
    if should_stop is None: should_stop=lambda: interrupted
    playing=asyncio.create_task(source.wait_for_playout())
    try:
        while not playing.done():
            if should_stop():
                source.clear_queue()
                break
            await asyncio.wait({playing},timeout=.01)
        await playing
    finally:
        if not playing.done():
            playing.cancel()
            await asyncio.gather(playing,return_exceptions=True)


async def run(request):
    from livekit import rtc
    sys.path.insert(0,'/usr/local/lib/agentcall')  # Installed Exmanager brain adapter.
    sys.path.insert(0,str(Path(__file__).with_name('voice')))
    import agentcall_realtime_bridge as voice
    record, account, identity = request['call'],request['account'],request['identity']
    config = server_voice_config(voice.BackendConfig.from_environment())
    os.environ['AGENTCALL_TTS_PHONE_FALLBACK']='0'
    rtc_url = os.environ['USERIO_MATRIX_RTC_AUTH_URL'].rstrip('/')
    server, token = account['homeserver'],account['access_token']
    room_id,target = record['room_id'],record['target']
    room_path = '/_matrix/client/v3/rooms/'+quote(room_id,safe='')
    async def api(method,path,payload=None):
        return await asyncio.to_thread(request_json,server+path,token,payload,method)
    events = await api('GET',room_path+'/state')
    if any(e.get('type')=='m.room.encryption' for e in events):
        raise ValueError('encrypted MatrixRTC key exchange unavailable; room was not downgraded')
    members = {e.get('state_key'):e.get('content',{}).get('membership') for e in events if e.get('type')=='m.room.member'}
    if set(k for k,v in members.items() if v=='join') != {account['matrix_user_id'],target}:
        raise ValueError('caller and recipient need an existing two-person room')
    device = identity.get('device_id')
    if not device: raise ValueError('caller device identity unavailable')
    prior = {e.get('event_id') for e in events if e.get('type')==MEMBER}
    openid = await api('POST','/_matrix/client/v3/user/'+quote(account['matrix_user_id'],safe='')+'/openid/request_token',{})
    sfu = await asyncio.to_thread(request_json,rtc_url+'/sfu/get','',
        {'room':room_id,'openid_token':openid,'device_id':device},'POST')
    media = rtc.Room(); source = rtc.AudioSource(48000,1,queue_size_ms=200)
    reader = asyncio.StreamReader(limit=1024*1024)
    ended = asyncio.Event(); transport_ready = asyncio.Event(); media_tasks = set(); engine_task = None; joined=False
    current_id = None
    loop = asyncio.get_running_loop()
    def terminate():
        ended.set()
        if not reader.at_eof(): reader.feed_eof()
    loop.add_signal_handler(signal.SIGTERM,terminate)
    async def receive(track):
        stream = rtc.AudioStream(track,sample_rate=48000,num_channels=1,frame_size_ms=20,capacity=100)
        try:
            await transport_ready.wait()
            async for event in stream:
                if ended.is_set(): break
                if len(reader._buffer)+len(event.frame.data)+3>256000:
                    raise BufferError('Matrix incoming audio exceeds finite buffer')
                reader.feed_data(voice.pack_audiosocket_frame(0x16,bytes(event.frame.data)))
        finally: await stream.aclose()
    @media.on('track_subscribed')
    def subscribed(track,publication,participant):
        if track.kind==rtc.TrackKind.KIND_AUDIO and participant.identity.startswith(target+':'):
            task=asyncio.create_task(receive(track));media_tasks.add(task)
            task.add_done_callback(lambda t: terminate() if not t.cancelled() and t.exception() else None)
    @media.on('participant_disconnected')
    def disconnected(participant):
        # SFU churn during ringing is not a recipient hangup. Start observing
        # the intended recipient only after the fresh answer was accepted.
        if transport_ready.is_set() and participant.identity.startswith(target+':'):
            terminate()

    class Writer:
        sample_rate=48000
        paces_audio=True
        def __init__(self): self.pending=bytearray();self.closed=False
        def write(self,data): self.pending.extend(data)
        async def drain(self):
            import struct
            while len(self.pending)>=3:
                length=struct.unpack('!H',self.pending[1:3])[0]
                if len(self.pending)<3+length: break
                kind=self.pending[0];pcm=bytes(self.pending[3:3+length]);del self.pending[:3+length]
                if kind==0x16:
                    await source.capture_frame(rtc.AudioFrame(pcm,48000,1,len(pcm)//2))
                elif kind==voice.HANGUP_KIND: terminate()
        def close(self): self.closed=True;terminate()
        async def finish_audio(self,*,should_stop=None,interrupted=False):
            await finish_rtc_audio(source,should_stop,interrupted=interrupted)
        def is_closing(self):return self.closed
        async def wait_closed(self):pass
        def get_extra_info(self,name,default=None):return default

    class Broker:
        async def emit(self,event):
            # Preserve the engine's exact event/transcript semantics.
            print(json.dumps(event,ensure_ascii=False),flush=True)
    broker=Broker(); prepared={};writer=Writer()
    async def matrix_answer(call_id,hung_up):return not ended.is_set()
    async def matrix_hangup(call_id):terminate()
    state_key='_'+account['matrix_user_id']+'_'+device+'_m.call'
    state_path=room_path+'/state/'+MEMBER+'/'+quote(state_key,safe='')
    async def originate(call_id):
        nonlocal current_id,engine_task,joined
        current_id=call_id
        print(json.dumps({'event':'prepared','callId':call_id}),flush=True)
        prepared[call_id]=bind_prepared(prepared[call_id],record)
        await media.connect(sfu['url'],sfu['jwt'],rtc.RoomOptions(connect_timeout=15))
        await media.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track('ИИ',source),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        started=int(time.time()*1000)
        result=await api('PUT',state_path,{'application':'m.call','call_id':'','device_id':device,
            'expires':(record['max_seconds']+90)*1000,'foci_preferred':[{'type':'livekit',
                'livekit_service_url':rtc_url,'livekit_alias':room_id}],
            'focus_active':{'focus_selection':'oldest_membership','type':'livekit'},
            'm.call.intent':'audio','membershipID':str(uuid.uuid4()),'scope':'m.room'})
        joined=True
        await api('PUT',room_path+'/send/'+RING+'/'+record['call_id'],{
            'm.mentions':{'user_ids':[target]},'notification_type':'ring',
            'm.relates_to':{'event_id':result['event_id'],'rel_type':'m.reference'},
            'sender_ts':started,'lifetime':60000,'m.call.intent':'audio'})
        await broker.emit({'event':'ringing','callId':call_id})
        deadline=time.monotonic()+60
        while not ended.is_set() and time.monotonic()<deadline:
            if fresh_answer(await api('GET',room_path+'/state'),target,started,prior,rtc_url):break
            await asyncio.sleep(1)
        else:
            await broker.emit({'event':'ended','callId':call_id,'reason':'unanswered'})
            raise TimeoutError('recipient did not answer')
        reader.feed_data(voice.pack_audiosocket_frame(voice.UUID_KIND,uuid.UUID(call_id).bytes))
        transport_ready.set()
        engine_task=asyncio.create_task(voice.handle_call(reader,writer,broker,config,prepared,
            hangup=matrix_hangup,answer_gate=matrix_answer))

    controller=voice.CallController(config,prepared,originate=originate)
    try:
        # This is the same production preparation and conversation path as GSM.
        await controller.prepare(record['greeting'] or record['purpose'],context=record['purpose'],repeat=1,
            agent_context=dict(record.get('agent_context') or {}, voice_transport='matrix', voice_target=record['target']))
        if engine_task:
            done,_=await asyncio.wait({engine_task},timeout=record['max_seconds'])
            if not done:terminate()
            await asyncio.wait_for(engine_task,timeout=config.request_timeout+10)
        await source.wait_for_playout()
    finally:
        terminate()
        if engine_task and not engine_task.done():
            engine_task.cancel();await asyncio.gather(engine_task,return_exceptions=True)
        for task in media_tasks:task.cancel()
        await asyncio.gather(*media_tasks,return_exceptions=True)
        try:
            try: await media.disconnect()
            finally: await source.aclose()
        finally:
            if joined:await api('PUT',state_path,{})


def main():
    try: asyncio.run(run(json.loads(sys.stdin.readline(65537))))
    except Exception as error:
        print(json.dumps({'event':'error','reason':type(error).__name__}),flush=True)
        raise SystemExit(1) from None
    finally:
        # Release callback cycles while LiveKit's native client still exists.
        gc.collect()


if __name__=='__main__': main()
