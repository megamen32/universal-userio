"""An uncertain native SDK turn must leave an audible status without a replay."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

import pytest

import test_voice_answer_first as fixtures

voice_env = fixtures.voice_env


def test_unsettled_turn_keeps_the_same_call_open_for_a_new_question(voice_env):
    bridge = fixtures.bridge
    admitted = []
    local_questions = []
    first = 'Выполни моё поручение.'
    second = 'Что уже известно об инциденте?'
    hangup = 'Заверши звонок.'
    goodbye = 'Хорошо, завершаю звонок.'

    class UnavailableNativeBrain:
        def __init__(self, *_args):
            self.history = []

        def reply_stream(self, text, _on_phrase):
            admitted.append(text)
            try:
                raise HTTPError('http://localhost/internal/voice/turn', 404, 'Not Found', {}, None)
            except HTTPError:
                raise bridge.NativeTurnUnsettled('Secretary voice outcome is unknown') from None

    def local_reply(text):
        local_questions.append(text)
        return fixtures.ANSWER_TEXT

    async def scenario():
        warning_spoken = asyncio.Event()
        followup_spoken = asyncio.Event()

        def on_event(event):
            if event.get('speaker') != 'agent':
                return
            text = event.get('text', '')
            if 'не повтор' in text.lower():
                warning_spoken.set()
            if text == fixtures.ANSWER_TEXT:
                followup_spoken.set()

        async def utterances():
            yield fixtures.VOICE * 20
            try:
                await asyncio.wait_for(warning_spoken.wait(), timeout=4)
            except asyncio.TimeoutError:
                return
            yield fixtures.VOICE * 21
            try:
                await asyncio.wait_for(followup_spoken.wait(), timeout=2)
            except asyncio.TimeoutError:
                return
            yield fixtures.VOICE * 22

        heard = iter((first, second, hangup))
        return await fixtures.run_prepared_call(
            lambda pcm: next(heard), utterances(), on_event=on_event,
            agent_context={'session_id': 'bound-session'}, reply=local_reply,
        )

    with patch.object(bridge, 'FastAgentIncidentBrain', UnavailableNativeBrain):
        sent, events = asyncio.run(scenario())
    spoken = fixtures.agent_turns(events)
    assert fixtures.ANSWER_TEXT in spoken, spoken
    assert spoken[-1] == goodbye
    assert events[-1]['event'] == 'ended'
    assert len({event['callId'] for event in events if event.get('event') == 'transcript_final'}) == 1
    assert len(sent) >= 4  # Greeting, warning, follow-up and explicit goodbye all have PCM.
    assert admitted == [first]
    assert local_questions == [second]


def test_noticeplace_call_reports_bound_investigation_without_the_sdk(voice_env):
    from noticeplace_voice_context import NoticePlaceVoiceStore, bound_investigation_answer

    case_id = 'inc_' + 'a' * 32
    binding = {'event_type': 'noticeplace.call', 'incident_id': case_id, 'session_id': 'bound-session'}
    question = 'Запустилась ли сессия расследования?'
    verified_answer = 'К этому инциденту уже привязана сессия расследования.'

    class UnavailableNativeBrain:
        def __init__(self, *_args):
            self.history = []

        def reply_stream(self, _text, _on_phrase):
            raise AssertionError('A bound incident status request must not go to the SDK')

    with patch.object(fixtures.bridge, 'FastAgentIncidentBrain', UnavailableNativeBrain), \
         patch.object(NoticePlaceVoiceStore, 'incident', return_value={'id': case_id, 'has_session': True}) as read:
        sent, events = asyncio.run(fixtures.run_prepared_call(
            lambda pcm: question, [fixtures.VOICE * 20], agent_context=binding,
        ))
        assert not bound_investigation_answer(dict(binding, call_mode='incoming_guest'), question)
        assert not bound_investigation_answer(dict(binding, call_mode='qa_scoped'), question)
        assert not bound_investigation_answer(binding, 'Запусти сессию расследования.')
        read.assert_called_once_with(case_id)
    assert fixtures.agent_turns(events) == [fixtures.GREETING_TEXT, verified_answer]
    assert len(sent) >= 2


def test_cleanup_deadline_leaves_audio_open_while_native_worker_finishes(voice_env):
    bridge = fixtures.bridge
    release_worker = threading.Event()
    real_wait = asyncio.wait

    class SlowNativeBrain:
        def reply_stream(self, _text, on_phrase):
            on_phrase('Проверяю состояние.')
            release_worker.wait(timeout=3)
            raise RuntimeError('Late worker exit')

    async def scenario():
        hung_up = asyncio.Event()
        late_error_logged = asyncio.Event()

        async def expire_cleanup(tasks, *, timeout=None, return_when=asyncio.ALL_COMPLETED):
            return await real_wait(tasks, timeout=0, return_when=return_when)

        def log(message):
            if 'detached voice turn failed' in message:
                late_error_logged.set()

        speech = SimpleNamespace(
            _synthesize_with_fallback=AsyncMock(side_effect=RuntimeError('TTS unavailable'))
        )
        with patch.object(bridge.asyncio, 'wait', side_effect=expire_cleanup), \
             patch.object(bridge, '_log', side_effect=log):
            try:
                with pytest.raises(bridge.NativeTurnUnsettled, match='cleanup deadline'):
                    await bridge.stream_voice_turn(
                        fixtures.Writer(), AsyncMock(), 'test-call', 'Проверь.', SlowNativeBrain(),
                        speech, hung_up, asyncio.Event(), peer_fields={},
                    )
                assert not hung_up.is_set()
            finally:
                release_worker.set()
            await asyncio.wait_for(late_error_logged.wait(), timeout=2)

    asyncio.run(scenario())
