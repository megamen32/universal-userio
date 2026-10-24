"""Voice answer-first regressions (canary mavis08, 2026-10-24).

Two measured defects of the live MatrixRTC canary:

D1. The first audible sound of a prepared call was the attention prompt
    ("Алло, алло, алло."), while the transcript announced the greeting that
    was only spoken afterwards.  The remote engine recognized the wake phrase
    as a substantive turn, so the caller's own question arrived as turn 2.

D2. The call ended after turn 1 although more caller turns were expected: a
    failed recognition of one caller utterance tore the whole call down, so
    the caller's next utterance was never answered.

Both tests drive the real bridge turn loop with the real recognizer; no
provider, phone, network or container is used.
"""
import asyncio
import importlib
import importlib.util
import struct
import sys
import threading
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


ROOT = Path(__file__).resolve().parents[1] / "src/universal_userio/voice"
PATH = ROOT / "agentcall_realtime_bridge.py"

spec = importlib.util.spec_from_file_location("voice_answer_first_bridge", PATH)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

VOICE = struct.pack("<160h", *([1000, -1000] * 80))  # 20 ms of 8 kHz PCM
# One second of real PCM16 per marker: the engine resamples it for playback.
GREETING_PCM = struct.pack("<16000h", *([1000, -1000] * 8000))
ATTENTION_PCM = struct.pack("<16000h", *([3000, -3000] * 8000))
ANSWER_PCM = struct.pack("<16000h", *([2000, -2000] * 8000))
GREETING_TEXT = "Здравствуйте. Проверим, что звук работает в обе стороны."
ATTENTION_TEXT = "Алло, алло, алло."
ANSWER_TEXT = "Ответ на реплику собеседника."
ACK_TEXT = "Подождите."


@pytest.fixture
def voice_env(tmp_path, monkeypatch):
    """Keep the engine away from operator identities, archive and STT."""
    monkeypatch.setenv("AGENTCALL_RECORDING_DIRECTORY", str(tmp_path / "recordings"))
    monkeypatch.setenv("EXMANAGER_VOICE_CONFIG_FILE", str(tmp_path / "absent-config"))
    monkeypatch.delenv("EXMANAGER_VOICE_URL", raising=False)
    monkeypatch.setattr(bridge, "INCOMPLETE_STT_DIRECTORY", tmp_path / "incomplete-stt")
    monkeypatch.syspath_prepend(str(ROOT))
    transcribe = importlib.import_module("agentcall_recording_transcribe")
    monkeypatch.setattr(transcribe, "submit_transcription", lambda *a: {"status": "QUEUED"})
    yield


class Writer:
    def close(self):
        pass

    async def wait_closed(self):
        pass


async def run_prepared_call(transcribe, utterances, *, on_event=None, timeout=30):
    """One prepared call through the real turn loop and the real recognizer."""
    sent: list = []
    events: list = []
    call_id = uuid.uuid4()

    class Pipeline:
        """The surface of the real LocalSpeechPipeline that the bridge touches."""

        is_digital_silence = staticmethod(lambda pcm: False)

        def __init__(self, config):
            self.config = config
            self.history = [{"role": "system", "content": config.system_prompt}]

        def opening(self, *_args):
            raise AssertionError("opening already prepared")

        def warm_voice(self):
            pass

        def close(self):
            pass

        def transcribe(self, pcm):
            return transcribe(pcm)

        def reply(self, text):
            return ANSWER_TEXT

        def synthesize(self, text):
            return ANSWER_PCM

    class Broker:
        async def emit(self, event):
            events.append(event)
            if on_event is not None:
                on_event(event)

    async def capture(audio_queue, utterance_queue, detector, **_kwargs):
        for item in utterances:
            await utterance_queue.put(item)
        await utterance_queue.put(None)

    async def send(writer, chunks, hung_up, *args, **kwargs):
        for chunk in chunks:
            sent.append(chunk)

    async def ready(*args, **kwargs):
        return True

    async def pump(*args, **kwargs):
        await asyncio.Future()

    async def hangup(*args, **kwargs):
        pass

    prepared = bridge.PreparedCall(
        greeting_text=GREETING_TEXT,
        greeting_pcm=GREETING_PCM,
        attention_text=ATTENTION_TEXT,
        attention_pcm=ATTENTION_PCM,
        context="Проверка голоса",
        agent_context={},
        created_at=0,
    )
    config = bridge.BackendConfig.from_environment()
    reader = bridge.BytesReader(bridge.pack_audiosocket_frame(bridge.UUID_KIND, call_id.bytes))
    with patch.object(bridge, "LocalSpeechPipeline", Pipeline), \
         patch.object(bridge, "read_audiosocket_frame",
                      return_value=(bridge.UUID_KIND, call_id.bytes)), \
         patch.object(bridge, "pump_audio", side_effect=pump), \
         patch.object(bridge, "capture_remote_audio", side_effect=capture), \
         patch.object(bridge, "wait_for_sip_answer", side_effect=ready), \
         patch.object(bridge, "send_pcm_stream", side_effect=send), \
         patch.object(bridge, "load_wait_prompts",
                      return_value=((ACK_TEXT, ANSWER_PCM),)), \
         patch.object(bridge.CallController, "_synthesize_with_fallback",
                      new=AsyncMock(return_value=ANSWER_PCM)):
        await asyncio.wait_for(
            bridge.handle_call(reader, Writer(), Broker(), config,
                               {str(call_id): prepared}, hangup=hangup),
            timeout,
        )
    return sent, events


def agent_turns(events):
    """Announced agent turns without the per-turn acknowledgement phrase."""
    return [event["text"] for event in events
            if event.get("event") == "transcript_final" and event.get("speaker") == "agent"
            and event.get("text") != ACK_TEXT]


def test_first_audible_chunk_is_the_announced_greeting(voice_env):
    """D1: the first sound of a prepared call is the announced greeting."""
    async def scenario():
        return await run_prepared_call(
            lambda pcm: "Привет. Проверка связи. Код 453 841.",
            [VOICE * 20, None],
        )

    sent, events = asyncio.run(scenario())
    assert sent, "the call produced no audio at all"
    assert sent[0] == GREETING_PCM, "the first audible chunk is not the announced greeting"
    assert ATTENTION_PCM not in sent, "the attention prompt was played as call audio"
    assert sent.count(GREETING_PCM) == 1, "the greeting was spoken more than once"
    assert agent_turns(events)[0] == GREETING_TEXT, "the announced text is not the greeting"


def test_session_survives_a_failed_recognition_and_answers_the_next_utterance(voice_env):
    """D2: one failed recognition must not close the call after turn 1."""
    answered = threading.Event()
    state = {"calls": 0}

    def transcribe(pcm):
        state["calls"] += 1
        if state["calls"] == 1:
            return "Алло, алло, алло."
        if state["calls"] == 2:
            # The caller speaks again only after turn 1 was answered; the
            # recognition of that utterance fails like in the canary run.
            answered.wait(5)
            raise RuntimeError("stt provider unavailable")
        return "Привет. Проверка связи. Код 453 841."

    def on_event(event):
        if event.get("event") == "transcript_final" and event.get("speaker") == "agent" \
                and event.get("text") == ANSWER_TEXT:
            answered.set()

    async def scenario():
        return await run_prepared_call(transcribe, [VOICE * 20] * 3, on_event=on_event)

    sent, events = asyncio.run(scenario())
    turns = agent_turns(events)
    assert len(turns) == 3, (
        "the session closed after turn 1: the caller's next utterance was never "
        f"answered (agent turns: {turns!r})"
    )
    assert turns[0] == GREETING_TEXT
    assert turns[1] == ANSWER_TEXT
    assert events[-1].get("event") == "ended", "the session did not end cleanly"
    assert state["calls"] == 3, "the recognizer stopped after the failed utterance"
