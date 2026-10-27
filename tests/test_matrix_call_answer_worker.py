"""The Matrix callee leg: the person comes from the verified registry, not the request."""
import asyncio
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CALLER = "@bezrabotnyi:chat.bezrabotnyi.com"
QA_CALLER = "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"
OWNER = "5e438326-6339-4ac8-a328-59dae1d6241f"
QA_PERSON = "9b4ab5e7-546e-4df9-a4c9-5c28f850cd26"


def load(name, relative):
    """Load a voice module as the package module it is in production."""
    if str(ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ROOT / "src"))
    module = importlib.import_module(relative)
    return module


def test_the_person_is_resolved_from_the_verified_registry():
    module = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    identities = {"matrix:" + CALLER: OWNER, "matrix:" + QA_CALLER: QA_PERSON}
    assert module.resolve_person(CALLER, identities) == OWNER
    assert module.resolve_person(QA_CALLER, identities) == QA_PERSON
    with pytest.raises(PermissionError):
        module.resolve_person("@stranger:chat.bezrabotnyi.com", identities)


def test_the_answer_request_cannot_carry_the_person(tmp_path):
    module = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    intake = load("matrix_inbound_calls", "universal_userio.matrix_inbound_calls")
    # Both sides of the private control protocol use the same action name.
    assert module.CALLEE_ACTION == intake.CALLEE_ACTION
    request = {
        "action": module.CALLEE_ACTION,
        "call": {"call_id": "c1", "room_id": "!r:chat.bezrabotnyi.com", "caller": CALLER,
                 "focus": {"type": "livekit", "livekit_service_url": "https://rtc.example/sfu/get"},
                 "expires_ms": 240000, "max_seconds": 120},
        "account": {"id": "matrix:1", "homeserver": "https://matrix.example",
                    "access_token": "t", "matrix_user_id": "@aiadmin:chat.bezrabotnyi.com"},
        "identity": {"device_id": "D"},
        "rtc_auth_url": "https://rtc.example/sfu/get",
    }
    # The worker resolves the person itself; a person in the request is ignored.
    assert "person" not in request["call"]
    identities = {"matrix:" + CALLER: OWNER}
    assert module.resolve_person(request["call"]["caller"], identities) == OWNER


def test_the_engine_reads_the_first_uuid_frame_of_the_callee_reader():
    """handle_call requires the UUID frame first; the callee reader must send it."""
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")

    call_id = str(uuid.uuid4())
    reader = asyncio.StreamReader(limit=1024 * 1024)
    reader.feed_data(bridge.pack_audiosocket_frame(bridge.UUID_KIND, uuid.UUID(call_id).bytes))

    async def check():
        kind, payload = await bridge.read_audiosocket_frame(reader)
        return kind, payload

    kind, payload = asyncio.run(check())
    assert kind == bridge.UUID_KIND
    assert len(payload) == 16
    assert uuid.UUID(bytes=payload) == uuid.UUID(call_id)


def test_the_callee_audio_sink_is_a_synchronous_writer_with_an_awaitable_drain():
    """The engine writes audio synchronously and awaits the drain."""
    import inspect
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")

    class Source:
        def __init__(self):
            self.frames = []

        async def capture_frame(self, frame):
            self.frames.append(frame)

    class Rtc:
        AudioFrame = staticmethod(lambda data, rate, channels, samples: data)

    source = Source()
    writer = worker._RtcWriter(source, asyncio.Event(), Rtc)
    # The engine writes synchronously and awaits the drain.
    assert not inspect.iscoroutinefunction(writer.write)
    assert inspect.iscoroutinefunction(writer.drain)

    pcm = b"\x01\x02" * 160
    writer.write(bridge.pack_audiosocket_frame(0x16, pcm))
    assert source.frames == []

    async def drain():
        await writer.drain()

    asyncio.run(drain())
    assert source.frames == [pcm]


def test_the_control_stream_dispatches_the_answer_action(monkeypatch):
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")
    calls = []

    async def fake_answer(request, config, *, broker, writer):
        calls.append(request)
        return {"type": "receipt", "state": "completed", "text": "Здравствуйте."}

    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    monkeypatch.setattr(worker, "answer_incoming_call", fake_answer)
    monkeypatch.setitem(sys.modules, "universal_userio.voice.matrix_call_answer_worker", worker)

    controller = bridge.CallController.__new__(bridge.CallController)
    controller.config = object()
    controller.prepared_calls = {}
    controller._matrix_transport_busy = False
    controller._telegram_transport_busy = False

    request = {"action": "matrix_answer_transport",
               "call": {"call_id": "c1", "room_id": "!r:chat.bezrabotnyi.com", "caller": CALLER},
               "account": {"id": "matrix:1", "homeserver": "https://matrix.example",
                           "access_token": "t", "matrix_user_id": "@aiadmin:chat.bezrabotnyi.com"},
               "identity": {"device_id": "D"},
               "rtc_auth_url": "https://rtc.example/sfu/get"}
    written = []

    class Writer:
        def write(self, data):
            written.append(data)

        async def drain(self):
            return None

        def close(self):
            return None

        async def wait_closed(self):
            return None

    class Reader:
        async def readline(self):
            return (json.dumps(request) + "\n").encode()

    asyncio.run(controller.accept(Reader(), Writer()))
    assert len(calls) == 1
    assert calls[0]["call"]["caller"] == CALLER
    assert written and json.loads(written[0])["type"] == "receipt"


def test_the_local_contract_from_the_request_to_the_engine_prepared_call():
    """build_answer_request -> the engine uuid frame -> the prepared call lookup.

    No RTC and no model: the wire call id stays room-scoped, the internal
    engine uuid is a real uuid, and the engine finds the prepared call by it.
    """
    import uuid as uuid_module
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    intake = load("matrix_inbound_calls", "universal_userio.matrix_inbound_calls")
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")

    call = intake.InboundMatrixCall(
        room_id="!r:chat.bezrabotnyi.com", event_id="$r1", caller=CALLER, call_id="",
        focus={"type": "livekit", "livekit_service_url": "https://rtc.example/sfu/get"},
        expires_ms=240000, origin_server_ts=1, joined=("@aiadmin:chat.bezrabotnyi.com", CALLER),
        membership_id="M1")
    request = intake.build_answer_request(
        call,
        account={"id": "matrix:1", "matrix_user_id": "@aiadmin:chat.bezrabotnyi.com"},
        secret={"homeserver": "https://matrix.example", "access_token": "t",
                "matrix_user_id": "@aiadmin:chat.bezrabotnyi.com"},
        identity={"device_id": "D"},
        rtc_auth_url="https://rtc.example/sfu/get")

    # The wire call id is room-scoped, exactly like the working caller.
    assert request["call"]["call_id"] == ""
    # The internal engine uuid is a real uuid the engine can frame and look up.
    engine_call_id = request["call"]["engine_call_id"]
    assert uuid_module.UUID(engine_call_id)

    prepared = {engine_call_id: object()}

    async def read_first():
        # The reader belongs to the running loop of the engine.
        reader = asyncio.StreamReader(limit=1024 * 1024)
        reader.feed_data(bridge.pack_audiosocket_frame(bridge.UUID_KIND, uuid_module.UUID(engine_call_id).bytes))
        return await bridge.read_audiosocket_frame(reader)

    kind, payload = asyncio.run(read_first())
    assert kind == bridge.UUID_KIND and len(payload) == 16
    # The engine finds the prepared call by exactly that uuid.
    assert str(uuid_module.UUID(bytes=payload)) in prepared
    # And the request never carries a person: the voice host resolves it.
    assert "person" not in request["call"]


def test_the_control_broker_reads_the_real_safe_event_contract():
    """The engine emits safe_event: the name in 'event', the text in 'text'."""
    import asyncio
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")

    class Inner:
        def __init__(self):
            self.events = []

        async def accept(self, reader, writer):
            return None

        async def emit(self, event):
            self.events.append(event)

    async def check():
        inner = Inner()
        broker = worker._ControlBroker(inner, None)
        # The greeting arrives first, then the answer of the Secretary.
        # The whole agent transcript is the transport text of the call.
        await broker.emit(bridge.safe_event("transcript_final", "c1", speaker="agent",
                                           text="Здравствуйте. Я голосовой секретарь."))
        await broker.emit(bridge.safe_event("transcript_final", "c1", speaker="agent",
                                           text="Секунду, пожалуйста.", phase="progress"))
        await broker.emit(bridge.safe_event("transcript_final", "c1", speaker="agent",
                                           text="Здравствуйте! Я веду ваши личные дела."))
        assert broker.transcript == (
            "Здравствуйте. Я голосовой секретарь. Секунду, пожалуйста. "
            "Здравствуйте! Я веду ваши личные дела.")
        # The remote side is never the spoken text of the call.
        await broker.emit(bridge.safe_event("transcript_final", "c1", speaker="remote",
                                           text="Алло?"))
        assert "Алло?" not in broker.transcript
        assert len(inner.events) == 4

    asyncio.run(check())
