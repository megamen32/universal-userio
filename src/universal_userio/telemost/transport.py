"""Receive-only Telemost signaling adapted from WhiteTransport's joiner.

No publisher, local tracks, tunneling, cookies or automatic rejoin. The SFU may
provide mixed audio; a track identifier is never asserted to be a person's name.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote, urlsplit
import uuid
import wave

from . import meeting_url

API='https://cloud-api.yandex.ru/telemost_front/v2/telemost'
ORIGIN='https://telemost.yandex.ru'
# SDK6 capability negotiation from WhiteTransport telemost/api.go.
CAPABILITIES={'offerAnswerMode': ['SEPARATE'], 'initialSubscriberOffer': ['ON_HELLO'], 'slotsMode': ['FROM_CONTROLLER'], 'simulcastMode': ['DISABLED', 'STATIC'], 'selfVadStatus': ['FROM_SERVER', 'FROM_CLIENT'], 'dataChannelSharing': ['TO_RTP'], 'videoEncoderConfig': ['NO_CONFIG', 'ONLY_INIT_CONFIG', 'RUNTIME_CONFIG'], 'dataChannelVideoCodec': ['VP8', 'UNIQUE_CODEC_FROM_TRACK_DESCRIPTION'], 'bandwidthLimitationReason': ['BANDWIDTH_REASON_DISABLED', 'BANDWIDTH_REASON_ENABLED'], 'sdkDefaultDeviceManagement': ['SDK_DEFAULT_DEVICE_MANAGEMENT_DISABLED', 'SDK_DEFAULT_DEVICE_MANAGEMENT_ENABLED'], 'joinOrderLayout': ['JOIN_ORDER_LAYOUT_DISABLED', 'JOIN_ORDER_LAYOUT_ENABLED'], 'pinLayout': ['PIN_LAYOUT_DISABLED'], 'sendSelfViewVideoSlot': ['SEND_SELF_VIEW_VIDEO_SLOT_DISABLED', 'SEND_SELF_VIEW_VIDEO_SLOT_ENABLED'], 'serverLayoutTransition': ['SERVER_LAYOUT_TRANSITION_DISABLED'], 'sdkPublisherOptimizeBitrate': ['SDK_PUBLISHER_OPTIMIZE_BITRATE_DISABLED', 'SDK_PUBLISHER_OPTIMIZE_BITRATE_FULL', 'SDK_PUBLISHER_OPTIMIZE_BITRATE_ONLY_SELF'], 'sdkNetworkLostDetection': ['SDK_NETWORK_LOST_DETECTION_DISABLED'], 'sdkNetworkPathMonitor': ['SDK_NETWORK_PATH_MONITOR_DISABLED'], 'publisherVp9': ['PUBLISH_VP9_DISABLED', 'PUBLISH_VP9_ENABLED'], 'svcMode': ['SVC_MODE_DISABLED', 'SVC_MODE_L3T3', 'SVC_MODE_L3T3_KEY'], 'subscriberOfferAsyncAck': ['SUBSCRIBER_OFFER_ASYNC_ACK_DISABLED', 'SUBSCRIBER_OFFER_ASYNC_ACK_ENABLED'], 'subscriberDtlsPassiveMode': ['SUBSCRIBER_DTLS_PASSIVE_MODE_DISABLED', 'SUBSCRIBER_DTLS_PASSIVE_MODE_ENABLED'], 'androidBluetoothRoutingFix': ['ANDROID_BLUETOOTH_ROUTING_FIX_DISABLED'], 'fixedIceCandidatesPoolSize': ['FIXED_ICE_CANDIDATES_POOL_SIZE_DISABLED'], 'sdkAndroidTelecomIntegration': ['SDK_ANDROID_TELECOM_INTEGRATION_DISABLED'], 'setActiveCodecsMode': ['SET_ACTIVE_CODECS_MODE_DISABLED', 'SET_ACTIVE_CODECS_MODE_VIDEO_ONLY'], 'publisherOpusDred': ['PUBLISHER_OPUS_DRED_DISABLED'], 'publisherOpusLowBitrate': ['PUBLISHER_OPUS_LOW_BITRATE_DISABLED'], 'sdkAndroidDestroySessionOnTaskRemoved': ['SDK_ANDROID_DESTROY_SESSION_ON_TASK_REMOVED_DISABLED'], 'svcModes': ['FALSE'], 'reportTelemetryModes': ['TRUE'], 'keepDefaultDevicesModes': ['FALSE']}


def hello(peer,room,credentials,service_name):
    name='Секретарь · запись встречи'
    return {'uid':str(uuid.uuid4()),'hello':{
        'participantMeta':{'name':name,'role':'SPEAKER','description':'Запись аудио и расшифровка',
                           'sendAudio':False,'sendVideo':False},
        'participantAttributes':{'name':name,'role':'SPEAKER','description':'Запись аудио и расшифровка'},
        'sendAudio':False,'sendVideo':False,'sendSharing':False,
        'participantId':peer,'roomId':room,'credentials':credentials,'serviceName':service_name,
        'disablePublisher':True,'disableSubscriber':False,'disableSubscriberAudio':False,
        'capabilitiesOffer':CAPABILITIES,'sdkInfo':{'implementation':'browser','version':'6.0.0','userAgent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36','hwConcurrency':2},
        'sdkInitializationId':str(uuid.uuid4())}}


def safe_media_url(value):
    u=urlsplit(value)
    host=u.hostname or ''
    if (u.scheme!='wss' or u.username or u.password or u.port not in {None,443}
        or not any(host==d or host.endswith('.'+d) for d in ('yandex.ru','yandex.net','yandex.com'))):
        raise ValueError('Telemost media endpoint unavailable')
    return value


class Receiver:
    def __init__(self, record, folder, save, stop):
        self.record,self.folder,self.save,self.stop=record,Path(folder),save,stop
        self.pc=None; self.tasks=[]; self.total_bytes=0; self.websocket=None
        self.started=time.monotonic(); self.pending=[]; self.remote=False
        self.seq=0; self.media_deadline=0; self.track_count=0
        self.record['protocol_events']=[]

    async def run(self):
        import aiohttp
        from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
        from aiortc.sdp import candidate_from_sdp
        headers={'Origin':ORIGIN,'Referer':ORIGIN+'/', 'Client-Instance-Id':str(uuid.uuid4()),
                 'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'}
        url=meeting_url(self.record['url'])
        path='/conferences/'+quote(url,safe='')
        deadline=self.started+self.record['max_seconds']
        timeout=aiohttp.ClientTimeout(total=None,sock_connect=15)
        async with aiohttp.ClientSession(headers=headers,timeout=timeout) as session:
            async def request(suffix,method='GET',body=None):
                async with session.request(method,API+path+suffix,json=body,allow_redirects=False,timeout=aiohttp.ClientTimeout(total=15)) as response:
                    if response.status!=200:
                        raise RuntimeError('telemost_http_'+str(response.status))
                    raw=await response.content.read(1024*1024+1)
                    if len(raw)>1024*1024:raise ValueError('provider response exceeds limit')
                    return json.loads(raw)
            connection=await request('/connection?next_gen_media_platform_allowed=true&display_name='+quote('Секретарь · запись встречи')+'&waiting_room_supported=true')
            if connection.get('connection_type')=='WAITING_ROOM':
                self.record['status']='waiting';self.save()
                interval=connection.get('client_configuration',{}).get('conference_check_access_interval_ms',3000)/1000
                while not self.stop.is_set() and time.monotonic()<deadline:
                    await asyncio.sleep(max(2,min(10,interval)))
                    if (await request('/waiting-rooms/check-access')).get('admitted'):
                        connection=await request('/connection?next_gen_media_platform_allowed=true&display_name='+quote('Секретарь · запись встречи')+'&waiting_room_supported=true')
                        break
                else:
                    self.record['status']='cancelled' if self.stop.is_set() else 'expired';self.save();return
            config=connection['client_configuration']
            media_url=safe_media_url(config['media_server_url'])
            ice=[RTCIceServer(urls=s['urls'],username=s.get('username'),credential=s.get('credential'))
                 for s in config.get('ice_servers',[])][:16]
            self.pc=RTCPeerConnection(RTCConfiguration(iceServers=ice))
            @self.pc.on('track')
            def on_track(track):
                if track.kind!='audio':return
                self.track_count+=1
                if self.track_count>8:
                    self.record['partial']=True;self.record['error']='audio_track_limit';self.stop.set();return
                self.tasks.append(asyncio.create_task(self.capture(track,self.track_count)))
            @self.pc.on('connectionstatechange')
            async def state_changed():
                if self.pc.connectionState=='connected':
                    self.record['status']='recording'; self.record['connected_at']=time.time();self.save()
                elif self.pc.connectionState in {'failed','closed'}:self.stop.set()
            async with session.ws_connect(media_url,timeout=15,max_msg_size=1024*1024) as ws:
                self.websocket=ws
                await ws.send_json(hello(connection['peer_id'],connection['room_id'],connection['credentials'],config['service_name']))
                next_ping=0;next_states=0
                while not self.stop.is_set() and time.monotonic()<deadline:
                    now=time.monotonic()
                    if now>=next_ping:
                        await ws.send_json({'uid':str(uuid.uuid4()),'ping':{}});next_ping=now+5
                    if now>=next_states:
                        await request('/request-states','POST',{'peers':[{'peer_id':connection['peer_id']}],
                                      'permissions':{},'conference':{'version':-1}})
                        next_states=now+max(10,min(60,config.get('state_check_interval_seconds',30)))
                    try:msg=await asyncio.wait_for(ws.receive(),timeout=1)
                    except asyncio.TimeoutError:continue
                    if msg.type!=aiohttp.WSMsgType.TEXT:
                        if msg.type in {aiohttp.WSMsgType.CLOSE,aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.ERROR}:
                            self.record['provider_close_code']=ws.close_code or (msg.data if isinstance(msg.data,int) else None);self.save();break
                        continue
                    payload=json.loads(msg.data)
                    if len(self.record['protocol_events'])<32:
                        self.record['protocol_events'].append(sorted(k for k in payload if k!='uid'));self.save()
                    if isinstance(payload.get('ack'),dict):
                        code=payload['ack'].get('status',{}).get('code')
                        if code and code!='OK':
                            self.record['provider_error_code']=str(code)[:100];self.save()
                    if payload.get('uid') and 'ack' not in payload:
                        await ws.send_json({'uid':payload['uid'],'ack':{'status':{'code':'OK','description':''}}})
                    if 'serverHello' in payload:
                        self.record['server_hello_fields']=sorted(payload['serverHello'].keys());self.save()
                        await ws.send_json({'uid':str(uuid.uuid4()),'setSlots':{'slots':[],
                            'audioSlotsCount':8,'key':1,'withSelfView':False,'shutdownAllVideo':True,'gridConfig':{}}})
                    if 'subscriberSdpOffer' in payload:
                        offer=payload['subscriberSdpOffer'];self.seq=offer['pcSeq']
                        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=offer['sdp'],type='offer'))
                        self.remote=True
                        for candidate in self.pending:await self.pc.addIceCandidate(candidate)
                        self.pending.clear()
                        # Never accept a sending direction even if the remote offer requests it.
                        for tr in self.pc.getTransceivers():tr.direction='recvonly' if tr.kind=='audio' else 'inactive'
                        answer=await self.pc.createAnswer();await self.pc.setLocalDescription(answer)
                        await ws.send_json({'uid':str(uuid.uuid4()),'subscriberSdpAnswer':{
                            'sdp':self.pc.localDescription.sdp,'pcSeq':self.seq}})
                    if 'webrtcIceCandidate' in payload:
                        item=payload['webrtcIceCandidate']
                        if item.get('target')!='SUBSCRIBER' or not item.get('candidate'):continue
                        candidate=candidate_from_sdp(item['candidate'].removeprefix('candidate:'))
                        candidate.sdpMid=item.get('sdpMid');candidate.sdpMLineIndex=item.get('sdpMlineIndex')
                        if self.remote:await self.pc.addIceCandidate(candidate)
                        else:self.pending.append(candidate)
                    if 'upsertDescription' in payload:
                        # Keep provider identity labels as metadata; don't attach names to an
                        # audio stream unless the provider supplies a verified binding.
                        descriptions=payload['upsertDescription'].get('description',[])
                        self.record['participants']=[{'id':str(d.get('id','')),
                            'name':str(d.get('meta',{}).get('name',''))[:200]} for d in descriptions[:100]]
                        self.save()
                self.record['status']='finalizing';self.save()
        if self.pc:await self.pc.close()
        for task in self.tasks:task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.record['status']='recorded' if any(t.get('bytes',0)>44 for t in self.record['tracks']) else 'no_audio'
        self.record['ended_at']=time.time();self.save()

    async def capture(self,track,index):
        from av import AudioResampler
        filename=f'track-{index}.wav'
        item={'index':index,'filename':filename,'label':'Аудиопоток '+str(index),
              'identity':'unmapped','started_offset':time.monotonic()-self.started,'bytes':0}
        self.record['tracks'].append(item);self.save()
        resampler=AudioResampler(format='s16',layout='mono',rate=16000)
        frames=0
        path=self.folder/filename
        try:
            with wave.open(str(path),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
                while not self.stop.is_set():
                    frame=await track.recv()
                    for pcm in resampler.resample(frame):
                        data=bytes(pcm.planes[0])[:pcm.samples*2]
                        self.total_bytes+=len(data)
                        if self.total_bytes>2*1024**3:
                            self.record['partial']=True;self.record['error']='recording_size_limit';self.stop.set();break
                        output.writeframesraw(data);frames+=pcm.samples
        except asyncio.CancelledError:pass
        except Exception as error:
            self.record['partial']=True;item['error']=type(error).__name__
        finally:
            if path.exists():
                import os
                os.chmod(path,0o600)
                item.update(bytes=path.stat().st_size,duration=frames/16000)
                digest=hashlib.sha256()
                with path.open('rb') as stream:
                    while data:=stream.read(1024*1024):digest.update(data)
                item['sha256']=digest.hexdigest()
            self.save()

    async def close(self):
        self.stop.set()
        if self.pc:await self.pc.close()
