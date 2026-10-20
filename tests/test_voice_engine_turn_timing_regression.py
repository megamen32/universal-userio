"""Offline voice-turn regressions (no providers or phone).

Caller speech must hold pending assistant PCM until VAD and serial recognition
settle; later confirmed speech must supersede older queued replies.
"""
import asyncio
import importlib.util
from pathlib import Path
import struct
import sys
import threading
from types import SimpleNamespace

import pytest


PATH = Path(__file__).resolve().parents[1] / 'src/universal_userio/voice/agentcall_realtime_bridge.py'
spec = importlib.util.spec_from_file_location('turn_timing_bridge', PATH)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

VOICE = struct.pack('<160h', *([1000, -1000] * 80))
SILENCE = bytes(320)


def test_question_with_internal_pause_is_collected_through_end_of_turn(monkeypatch):
    # Filtered live process environment confirmed 0.7s, not the source default.
    monkeypatch.setenv('AGENTCALL_END_SILENCE_SECONDS', '0.7')
    detector = bridge.build_turn_detector(bridge.BackendConfig.from_environment())
    # "Хорошо." followed by 420ms pause and "Как там сессия?". A short
    # endpoint splits this into two turns and allows an early answer.
    question = [VOICE] * 40 + [SILENCE] * 21 + [VOICE] * 60
    for frame in question:
        assert detector.push(frame) is None, 'answered before the question ended'
    emitted = []
    for frame in [SILENCE] * 35:
        pcm = detector.push(frame)
        if pcm:
            emitted.append(pcm)
    assert emitted == [b''.join(question)]


@pytest.mark.parametrize('transcript', ['Как там сессия?', ''])
def test_pending_older_chunk_waits_during_collection_and_stt(monkeypatch, transcript):
    """A previous answer cannot start while caller VAD and STT are active."""
    async def scenario():
        gate, hung = bridge.TurnPlaybackGate(), asyncio.Event()
        incoming, utterances, recognized = asyncio.Queue(), asyncio.Queue(), asyncio.Queue()
        detector = bridge.TurnDetector(end_silence_frames=35)
        capture = asyncio.create_task(bridge.capture_remote_audio(
            incoming, utterances, detector, turn_gate=gate))
        for _ in range(10):
            await incoming.put(VOICE)
        # Allow capture to confirm VAD, without reaching end-of-turn.
        await asyncio.sleep(0)
        assert detector.started and gate.pending == 1
        frames = []
        class Writer:
            def write(self, frame): frames.append(frame)
            async def drain(self): pass
        pcm = bridge.SpeechPCM(VOICE * 2, 8000, applied_speed=1.0)
        reply = asyncio.create_task(bridge.send_pcm_stream(Writer(), iter((pcm,)), hung, gate))
        await asyncio.sleep(.06)  # enough to send all 40ms if no gate exists
        assert frames == [], 'old answer began while caller was still speaking'
        for _ in range(35):
            await incoming.put(SILENCE)
        await incoming.put(None)
        await capture
        assert gate.pending == 1  # VAD ended; serial recognition still pending
        stt_ready, stt_release = threading.Event(), threading.Event()
        def transcribe(_):
            stt_ready.set()
            assert stt_release.wait(2)
            return transcript
        monkeypatch.setattr(bridge, 'retain_unrecognized_audio', lambda *_: 'private-test-evidence')
        recognition = asyncio.create_task(bridge.recognize_remote_audio(
            utterances, recognized, SimpleNamespace(transcribe=transcribe), Broker(), 'offline', {}, gate))
        assert await asyncio.to_thread(stt_ready.wait, 2)
        assert frames == [], 'old answer began while STT was still pending'
        stt_release.set()
        await recognition
        await asyncio.wait_for(reply, 2)
        assert gate.pending == 0
        assert len(frames) == (0 if transcript else 2)
        assert gate.is_set() is bool(transcript)

    asyncio.run(asyncio.wait_for(scenario(), 5))


def test_hangup_releases_a_pending_reply_without_sending_pcm():
    async def scenario():
        gate, hung = bridge.TurnPlaybackGate(), asyncio.Event()
        gate.begin_utterance()
        class Writer:
            def write(self, _): raise AssertionError('speech after hangup')
            async def drain(self): pass
        reply = asyncio.create_task(bridge.send_pcm_stream(Writer(),
            iter((bridge.SpeechPCM(VOICE, 8000, applied_speed=1.0),)), hung, gate))
        await asyncio.sleep(0)
        hung.set()
        await asyncio.wait_for(reply, 1)
    asyncio.run(scenario())


class Broker:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


@pytest.mark.parametrize('stage', ['model', 'synthesis', 'playback'])
def test_confirmed_followup_suppresses_older_turn_and_settles_history(monkeypatch, stage):
    """A confirmed follow-up invalidates an older pending answer."""
    async def scenario():
        stop, hung_up = asyncio.Event(), asyncio.Event()
        ready, release = threading.Event(), threading.Event()
        synth_ready, synth_release = asyncio.Event(), asyncio.Event()
        playback_ready, playback_release = asyncio.Event(), asyncio.Event()
        played = []
        broker = Broker()
        answer = 'Ответ на предыдущий вопрос.'
        followup = 'А сессия, которая начала чинить, не отображается?'

        class Brain:
            config = SimpleNamespace(fast_agent_timeout=1)
            history = []

            def reply_stream(self, text, on_phrase):
                if stage == 'model':
                    ready.set()
                    assert release.wait(2)
                on_phrase(answer)
                self.history.append((text, answer))
                return answer

        class Speech:
            config = SimpleNamespace(speech_speed=1.0)

            async def _synthesize_with_fallback(self, phrase):
                if stage == 'synthesis' and phrase == answer:
                    synth_ready.set()
                    await synth_release.wait()
                return bridge.SpeechPCM(VOICE * 2, 8000, applied_speed=1.0)

        class Writer:
            def write(self, frame):
                played.append(frame)

            async def drain(self):
                if stage == 'playback' and len(played) == 1:
                    playback_ready.set()
                    await playback_release.wait()

        writer = Writer()
        brain = Brain()
        turn = asyncio.create_task(bridge.stream_voice_turn(
            writer, broker, 'offline', 'Как там сессия?', brain, Speech(),
            hung_up, stop, peer_fields={}))
        if stage == 'model':
            assert await asyncio.to_thread(ready.wait, 2)
        elif stage == 'synthesis':
            await asyncio.wait_for(synth_ready.wait(), 2)
        else:
            await asyncio.wait_for(playback_ready.wait(), 2)
        sent_before_followup = len(played)

        utterances, recognized = asyncio.Queue(), asyncio.Queue()
        await utterances.put(bridge.CapturedPCM(VOICE, 26.472))
        await utterances.put(None)
        pipeline = SimpleNamespace(transcribe=lambda _: followup)
        await bridge.recognize_remote_audio(utterances, recognized, pipeline,
                                           broker, 'offline', {}, stop)
        assert await recognized.get() == followup
        release.set()
        synth_release.set()
        playback_release.set()
        assert await asyncio.wait_for(turn, 2) == answer
        assert len(played) == sent_before_followup, 'older response was spoken after confirmed follow-up'
        assert brain.history == [('Как там сессия?', answer)]
        # Native history settles once; the new turn can speak normally.
        stop.clear()
        await bridge.stream_voice_turn(writer, broker, 'offline', followup,
            brain, Speech(), hung_up, stop, peer_fields={}, fixed_answer='Проверяю сессию.')
        assert len(played) == sent_before_followup + 2
        assert len(brain.history) == 1

    asyncio.run(asyncio.wait_for(scenario(), 5))


@pytest.mark.parametrize('transcript,expected', [('', False), ('Стоп.', True)])
def test_only_confirmed_speech_interrupts_and_explicit_stop_is_preserved(transcript, expected, monkeypatch):
    async def scenario():
        utterances, recognized, stop = asyncio.Queue(), asyncio.Queue(), asyncio.Event()
        await utterances.put(VOICE)
        await utterances.put(None)
        monkeypatch.setattr(bridge, 'retain_unrecognized_audio', lambda *_: 'private-test-evidence')
        await bridge.recognize_remote_audio(utterances, recognized,
            SimpleNamespace(transcribe=lambda _: transcript), Broker(), 'offline', {}, stop)
        assert stop.is_set() is expected
        if transcript:
            assert await recognized.get() == transcript
        assert await recognized.get() is None

    asyncio.run(scenario())


def test_phone_brain_card_uses_one_shared_name_preserving_policy(monkeypatch, tmp_path):
    import json
    from dataclasses import replace
    monkeypatch.syspath_prepend(str(PATH.parent))
    import agentcall_fast_agent_worker as worker
    cards = []
    class Worker:
        alive = True
        def __init__(self, _python, boot, **_):
            cards.append(json.loads(Path(boot['card']).read_text()))
        def close(self): pass
    monkeypatch.setattr(worker, 'VoiceWorker', Worker)
    executable = tmp_path / 'fast-agent'
    executable.write_text('#!/usr/bin/python3\n')
    config = replace(bridge.BackendConfig.from_environment(), fast_agent_bin=str(executable),
                     fast_agent_home=str(tmp_path))
    brain = bridge.FastAgentIncidentBrain(config, {}, '')
    def context(_text):
        path = tmp_path / 'context.json'
        path.write_text('{}')
        return path
    monkeypatch.setattr(brain, '_write_context', context)
    try:
        brain._ensure_worker(('test-model', 'http://127.0.0.1:1', 'test-key'))
        effective = worker.compose_voice_instruction(cards[0]['instruction'])
        assert effective.count(worker.VOICE_LANGUAGE_POLICY) == 1
        assert worker.compose_voice_instruction(effective) == effective
        assert worker.status_agent_instruction().count(worker.VOICE_LANGUAGE_POLICY) == 1
        assert 'Джи-пи-ти Админ' in effective
        assert 'Английские названия технических механизмов и сокращения не зачитывай' not in effective
        assert 'Неизвестные машинные имена и идентификаторы пропускай' not in effective
        assert 'английских полей' not in worker.status_agent_instruction()
        assert 'Джи-пи-ти Админ' in bridge.text_for_speech('Откройте Джи-пи-ти Админ.')
        brain.agent_context['call_mode'] = 'incoming'
        status = brain._status_prompt('Какие сейчас активные инциденты?',
            {'incidents': [], 'total_in_scope': 0, 'truncated': False})
        assert 'английских полей' not in status
    finally:
        brain._dispose_worker()
