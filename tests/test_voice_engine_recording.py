"""Fast unit + focused wire integration, expected2s/max10s; no phone/provider.

Detects wrong audio direction, unsent TTS capture, broken alignment/headers,
silent quota failures and overwrite of an existing private call archive.
"""
import asyncio
import importlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
import uuid
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/'src/universal_userio/voice'
sys.path.insert(0,str(ROOT))
recording=importlib.import_module('agentcall_recording')
bridge=importlib.import_module('agentcall_realtime_bridge')


class RecordingTests(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp')
        self.addCleanup(directory.cleanup);self.root=Path(directory.name);self.now=0.0
        self.recorder=recording.CallRecorder(str(uuid.uuid4()),'incoming',{'peerRef':'opaque'},self.root,clock=lambda:self.now)
        self.addCleanup(self.recorder.finish)
    def read(self,name):
        with wave.open(str(self.recorder.path/name),'rb') as wav:
            self.assertEqual((1,2,8000),(wav.getnchannels(),wav.getsampwidth(),wav.getframerate()))
            return wav.getnframes(),wav.readframes(wav.getnframes())
    def test_actual_received_sent_are_aligned_and_private(self):
        self.now=.1;self.recorder.incoming(struct.pack('<h',1000)*160)
        self.now=.15;self.recorder.outgoing(struct.pack('<h',2000)*160)
        self.now=.3;manifest=self.recorder.finish();data=json.loads(manifest.read_text())
        n,caller=self.read('caller.wav');m,assistant=self.read('assistant.wav')
        self.assertEqual((2400,2400),(n,m))
        self.assertEqual(struct.pack('<h',1000)*160,caller[1600:1920])
        self.assertEqual(struct.pack('<h',2000)*160,assistant[2400:2720])
        self.assertEqual({'caller':1,'assistant':1},data['recordedFrames'])
        self.assertTrue(data['complete']);self.assertEqual('incoming',data['direction'])
        self.assertEqual({'caller':'caller.wav','assistant':'assistant.wav'},data['tracks'])
        self.assertEqual(manifest,self.recorder.finish())
        for p in (manifest,self.recorder.path/'caller.wav',self.recorder.path/'assistant.wav'):
            self.assertEqual(0o600,stat.S_IMODE(p.stat().st_mode))
            self.assertEqual((self.root.stat().st_uid,self.root.stat().st_gid),(p.stat().st_uid,p.stat().st_gid))
        self.assertEqual(0o700,stat.S_IMODE(self.recorder.path.stat().st_mode))
        # The owning transcriber's actual validator consumes our closed files.
        consumer=importlib.import_module('agentcall_recording_transcribe')
        with patch.dict(os.environ,{'AGENTCALL_RECORDING_DIRECTORY':str(self.root)}):
            self.assertEqual(manifest,consumer.validate_manifest(manifest)[0])
    def test_old_archive_is_never_overwritten(self):
        self.recorder.incoming(b'\x01\x01'*160);manifest=self.recorder.finish();before=manifest.read_bytes()
        duplicate=recording.CallRecorder(self.recorder.call_id,'outgoing',{},self.root)
        self.assertTrue(duplicate.failure)
        with self.assertRaises(RuntimeError):duplicate.finish()
        self.assertEqual(before,manifest.read_bytes())
    def test_limit_is_explicit_and_keeps_valid_partial_waves(self):
        with patch.object(recording,'MAX_CALL_SECONDS',.04):
            self.recorder.incoming(b'\x01\x01'*160)
            self.now=.1;self.recorder.incoming(b'\x02\x02'*160)
            manifest=self.recorder.finish()
        data=json.loads(manifest.read_text());self.assertFalse(data['complete'])
        self.assertIn('call_duration',data['recordingFailure'])
        self.assertEqual(320,self.read('caller.wav')[0])
    def test_archive_quota_does_not_delete_or_replace_old_data(self):
        old=self.root/'old.bin';old.write_bytes(b'old-recording')
        with patch.object(recording,'ARCHIVE_LIMIT',old.stat().st_size):
            blocked=recording.CallRecorder(str(uuid.uuid4()),'incoming',{},self.root)
            self.assertTrue(blocked.failure)
            with self.assertRaises(RuntimeError):blocked.finish()
        self.assertEqual(b'old-recording',old.read_bytes())
    def test_write_failure_marks_incomplete_without_losing_closed_partial_audio(self):
        self.recorder.incoming(b'\x01\x01'*160)
        with patch.object(self.recorder.tracks['caller']['wave'],'writeframesraw',side_effect=OSError('fixture disk full')):
            self.recorder.incoming(b'\x02\x02'*160)
        manifest=self.recorder.finish();data=json.loads(manifest.read_text())
        self.assertFalse(data['complete']);self.assertEqual('OSError',data['recordingFailure'])
        self.assertEqual(b'\x01\x01'*160,self.read('caller.wav')[1])
    def test_shared_guard_refuses_growth_before_mutation(self):
        total=sum(p.stat().st_size for p in self.root.rglob('*') if p.is_file())
        target=self.root/'transcript.json'
        with patch.object(recording,'ARCHIVE_LIMIT',total+2):
            with self.assertRaisesRegex(recording.RecordingLimit,'capacity_exhausted'):
                with recording.archive_guard(self.root,3):target.write_bytes(b'new')
        self.assertFalse(target.exists())
    def test_shared_archive_lock_wait_is_bounded(self):
        import fcntl,time
        fd=os.open(self.root/'.capacity.lock',os.O_RDWR)
        try:
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);started=time.monotonic()
            with self.assertRaisesRegex(recording.RecordingLimit,'capacity_busy'):
                with recording.archive_guard(self.root):pass
            self.assertLess(time.monotonic()-started,.6)
        finally:os.close(fd)


class SpeechPipelineTests(unittest.TestCase):
    def test_stt_upload_resamples_live_8khz_pcm_for_whisper(self):
        from dataclasses import replace
        pcm=struct.pack('<160h',*([1200,-1200]*80))
        captured={}
        original=bridge.pcm_to_wav
        def inspect_wav(samples,sample_rate=8000):
            captured.update(samples=samples,sample_rate=sample_rate)
            return original(samples,sample_rate)
        config=replace(bridge.BackendConfig.from_environment(),
            stt_url='https://stt.example/v1/audio/transcriptions',stt_api_key='test-stt-key')
        with patch.object(bridge,'pcm_to_wav',side_effect=inspect_wav), \
             patch.object(bridge,'_request',return_value=('{"text":"проверка"}'.encode(),'application/json')) as request:
            result=bridge.LocalSpeechPipeline(config).transcribe(pcm)
        self.assertEqual('проверка',result)
        self.assertEqual(48000,captured['sample_rate'])
        self.assertGreater(len(captured['samples']),5*len(pcm))
        self.assertEqual('Bearer test-stt-key',request.call_args.kwargs['headers']['Authorization'])


class WireTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_bootstrap_failure_does_not_lose_caller_or_close_early(self):
        from dataclasses import replace
        import time
        reader=asyncio.StreamReader();call=uuid.uuid4();events=[];sent=[];hangups=[]
        config=replace(bridge.BackendConfig.from_environment(),end_silence_seconds=.04,idle_timeout_seconds=2,speech_speed=1)
        prepared=bridge.PreparedCall('Привет.',b'\x01\x01'*160,'',b'\0'*320,'',{'call_mode':'incoming_guest'},time.time(),(('Секунду.',b'\0'*160),))
        reader.feed_data(bridge.pack_audiosocket_frame(bridge.UUID_KIND,call.bytes))
        for _ in range(12):reader.feed_data(bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,b'\x01\x01'*160))
        for _ in range(8):reader.feed_data(bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,b'\0'*320))
        class Pipeline:
            def __init__(self,config):self.config=config;self.history=[]
            def opening(self,*args):raise AssertionError('cached greeting expected')
            def transcribe(self,pcm):return 'Спасибо, пока.'
            def synthesize(self,text):raise AssertionError('provider disabled in this test')
        class Speech:
            def __init__(self,config,*args,**kwargs):self.config=config
            async def _synthesize_with_fallback(self,text):return b'\x01\x01'*160
        class Writer:
            def write(self,data):sent.append(data)
            async def drain(self):pass
            def close(self):pass
            async def wait_closed(self):pass
        class Broker:
            async def emit(self,event):events.append(event)
        async def ready(*args):return True
        async def hangup(value):hangups.append(value)
        with patch.object(recording,'CallRecorder',side_effect=PermissionError('fixture directory denied')), \
             patch.object(bridge,'LocalSpeechPipeline',Pipeline),patch.object(bridge,'CallController',Speech), \
             patch.object(bridge,'wait_for_sip_answer',side_effect=ready):
            await asyncio.wait_for(bridge.handle_call(reader,Writer(),Broker(),config,{str(call):prepared},hangup=hangup),4)
        self.assertEqual(['Спасибо, пока.'],[e['text'] for e in events if e.get('speaker')=='remote'])
        self.assertTrue(any('Не удалось начать полную запись' in e.get('reason','') for e in events))
        self.assertTrue(any(packet[0]==bridge.AUDIO_KIND for packet in sent))
        self.assertEqual([str(call)],hangups)
    async def test_partial_received_frame_is_preserved_and_marked_incomplete(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()/'.tmp') as directory:
            sink=recording.CallRecorder(str(uuid.uuid4()),'incoming',{},directory,clock=lambda:0)
            reader=asyncio.StreamReader();queue=asyncio.Queue();closed=asyncio.Event()
            reader.feed_data(bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,b'\x01\x01'*160)[:103]);reader.feed_eof()
            await bridge.pump_audio(reader,queue,closed,recorder=sink)
            data=json.loads(sink.finish().read_text());self.assertFalse(data['complete'])
            self.assertEqual('received_frame_incomplete',data['recordingFailure'])
            with wave.open(str(sink.path/'caller.wav'),'rb') as wav:self.assertEqual(b'\x01\x01'*50,wav.readframes(50))
    async def test_inbound_silence_and_speech_are_recorded_before_vad_queue(self):
        reader=asyncio.StreamReader();queue=asyncio.Queue();closed=asyncio.Event();seen=[]
        quiet=b'\0'*320;speech=b'\x01\x01'*320
        for pcm in (quiet,speech):reader.feed_data(bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,pcm))
        reader.feed_eof()
        sink=type('Sink',(),{'incoming':lambda self,pcm:seen.append(pcm)})()
        await bridge.pump_audio(reader,queue,closed,recorder=sink)
        self.assertEqual([quiet,speech],seen);self.assertEqual(quiet,await queue.get())

    async def test_hangup_queues_close_before_unblocking_finalizer(self):
        class ObservedQueue(asyncio.Queue):
            def __init__(self):
                super().__init__(maxsize=1)
                self.close_attempted=asyncio.Event()
            async def put(self,item):
                if item is None:self.close_attempted.set()
                await super().put(item)
        reader=asyncio.StreamReader();queue=ObservedQueue();closed=asyncio.Event()
        speech=b'\x01\x01'*320
        queue.put_nowait(speech)
        reader.feed_data(bridge.pack_audiosocket_frame(bridge.HANGUP_KIND))
        task=asyncio.create_task(bridge.pump_audio(reader,queue,closed))
        await asyncio.wait_for(queue.close_attempted.wait(),timeout=.2)
        self.assertFalse(closed.is_set())
        self.assertEqual(speech,queue.get_nowait())
        await task
        self.assertTrue(closed.is_set())
        self.assertIsNone(queue.get_nowait())
    async def test_writer_records_only_accepted_pcm_after_tempo(self):
        seen=[];sent=[]
        class Sink:
            failure=''
            def outgoing(self,pcm):seen.append(pcm)
            def fail(self,error):self.failure=str(error)
        class Writer:
            def write(self,data):sent.append(data)
            async def drain(self):pass
        writer=recording.RecordingWriter(Writer(),Sink())
        transformed=b'\x02\x02'*160
        with patch.object(bridge,'tempo_pcm',return_value=transformed):
            await bridge.send_pcm_stream(writer,iter((b'\x01\x01'*1600,)),asyncio.Event(),speed=1.5)
        self.assertEqual([transformed],seen)
        self.assertEqual([bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,transformed)],sent)
        class Refused(Writer):
            def write(self,data):raise BrokenPipeError()
        wrapper=recording.RecordingWriter(Refused(),Sink())
        with self.assertRaises(BrokenPipeError):wrapper.write(bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,b'\x03\x03'*160))
        self.assertEqual([transformed],seen)
    async def test_fragmented_writer_frames_are_not_guessed_as_pcm(self):
        seen=[]
        class Sink:
            failure=''
            def outgoing(self,pcm):seen.append(pcm)
            def fail(self,error):self.failure=str(error)
        class Writer:
            def write(self,data):pass
        wrapper=recording.RecordingWriter(Writer(),Sink())
        frame=bridge.pack_audiosocket_frame(bridge.AUDIO_KIND,b'\x01\x01'*160)
        wrapper.write(frame[:2]);self.assertEqual([],seen)
        wrapper.write(frame[2:]);self.assertEqual([frame[3:]],seen)


if __name__=='__main__':unittest.main()

from test_voice_engine_fast_agent import isolate_voice_engine
