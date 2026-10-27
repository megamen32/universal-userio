"""The Matrix callee leg: the person comes from the verified registry, not the request."""
import asyncio
import importlib.util
import json
import os
import stat
import sys
import types
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


def test_the_answer_leg_uses_the_server_speech_profile():
    """A Matrix call must never wait for the telephone device.

    The voice host may run the telephone profile, whose synthesis needs the
    physical phone. The callee leg reuses the existing Matrix transport
    profile, so the greeting and the conversation both speak on the server.
    """
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    transport = load("matrix_call_worker", "universal_userio.matrix_call_worker")
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")

    telephone = bridge.BackendConfig.from_environment()
    telephone = bridge.replace(telephone, tts_provider='android-phone',
                                tts_engine='silero',
                                tts_url='http://127.0.0.1:30802/synthesize')
    server = worker.server_voice_config(telephone)
    # The same existing profile the working Matrix caller uses.
    assert server == transport.server_voice_config(telephone)
    assert server.tts_provider == 'qwen-warm'
    assert server.tts_engine == 'silero'
    assert server.tts_url == 'http://127.0.0.1:30802/synthesize'
    # A server profile stays untouched.
    assert worker.server_voice_config(server) == server


def test_the_answer_action_imports_in_the_script_layout_of_the_voice_host():
    """The voice host runs the bridge as a script, so the action must import.

    The native service executes the bridge file as __main__ and publishes it
    under its flat module name. The callee leg has to be importable in that
    layout, and it has to use that very instance of the bridge.
    """
    import importlib.util
    bridge_path = ROOT / "src/universal_userio/voice/agentcall_realtime_bridge.py"
    spec = importlib.util.spec_from_file_location("agentcall_realtime_bridge", bridge_path)
    bridge = importlib.util.module_from_spec(spec)
    sys.modules["agentcall_realtime_bridge"] = bridge
    voice_path = str(ROOT / "src/universal_userio/voice")
    if voice_path not in sys.path:
        sys.path.insert(0, voice_path)
    try:
        spec.loader.exec_module(bridge)
        worker = importlib.import_module("matrix_call_answer_worker")
        assert worker.answer_incoming_call is not None
        # One identity: the worker speaks through the running bridge.
        assert worker.handle_call is bridge.handle_call
    finally:
        sys.modules.pop("agentcall_realtime_bridge", None)
        sys.modules.pop("matrix_call_answer_worker", None)


def test_the_identity_map_comes_from_the_supervised_secret_of_the_host(tmp_path, monkeypatch):
    """The identity map path is the supervised secret, never the request."""
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    # The supervised secret contract of the SDK adapter: a private, owned file.
    adapter = types.ModuleType("agentcall_secretary_voice")

    def private_file(name, settings=None):
        path = Path((settings or os.environ)[name])
        meta = path.stat()
        if meta.st_uid not in {0, os.geteuid()} or stat.S_IMODE(meta.st_mode) & 0o077:
            raise PermissionError("voice configuration must be private and service-owned")
        return path.read_text().strip()

    adapter.private_file = private_file
    monkeypatch.setitem(sys.modules, "agentcall_secretary_voice", adapter)
    identities = tmp_path / "identities.json"
    identities.write_text(json.dumps({"matrix:" + CALLER: OWNER}), encoding="utf-8")
    identities.chmod(0o600)
    monkeypatch.setenv("EXMANAGER_VOICE_IDENTITIES_FILE", str(identities))
    # A configuration without the identities path must still resolve the
    # supervised secret of this host instead of failing on a missing key.
    resolved = worker.load_identities({"fast_agent_model": "some-model"})
    assert resolved == {"matrix:" + CALLER: OWNER}
    # An explicit path wins over the environment.
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"matrix:" + QA_CALLER: QA_PERSON}), encoding="utf-8")
    other.chmod(0o600)
    assert worker.load_identities(
        {"EXMANAGER_VOICE_IDENTITIES_FILE": str(other)}) == {"matrix:" + QA_CALLER: QA_PERSON}


def test_early_incoming_audio_cannot_overtake_the_call_id():
    """The engine reads the call id first, so early audio has to wait for it.

    The remote audio can arrive while the callee still publishes its
    membership and synthesizes the greeting. That audio must not become the
    first frame of the reader.
    """
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")

    async def check():
        reader = asyncio.StreamReader(limit=1024 * 1024)
        transport_ready = asyncio.Event()
        stopped = asyncio.Event()
        released = asyncio.Event()

        async def frames():
            yield b"\x11\x22" * 160
            await released.wait()

        task = asyncio.create_task(worker.feed_incoming_audio(
            reader, frames(), transport_ready=transport_ready, stopped=stopped))
        await asyncio.sleep(0)
        # The remote audio arrived, but the call id is not in the reader yet.
        assert reader._buffer == b""
        call_id = str(uuid.uuid4())
        reader.feed_data(bridge.pack_audiosocket_frame(bridge.UUID_KIND, uuid.UUID(call_id).bytes))
        transport_ready.set()
        await asyncio.sleep(0)
        released.set()
        assert await task == 1
        kind, payload = await bridge.read_audiosocket_frame(reader)
        assert kind == bridge.UUID_KIND
        assert uuid.UUID(bytes=payload) == uuid.UUID(call_id)

    asyncio.run(check())


def test_the_callee_leg_has_no_undefined_name():
    """The callee leg runs in the voice host: every name must resolve.

    The leg answers a live call; a single missing constant would break the
    answer before the SDK turn starts.
    """
    import ast
    import builtins

    path = ROOT / "src/universal_userio/voice/matrix_call_answer_worker.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, ast.arg):
            defined.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                defined.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
    used = {node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    assert not sorted(used - defined)


def test_the_transport_receipt_follows_the_broker_contract():
    """The receipt is built from the broker that collected the transcript."""
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")
    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")

    class Inner:
        def __init__(self):
            self.events = []

        async def accept(self, reader, writer):
            return None

        async def emit(self, event):
            self.events.append(event)

    async def check():
        broker = worker._ControlBroker(Inner(), None)
        await broker.emit(bridge.safe_event("transcript_final", "c1", speaker="agent",
                                           text="Здравствуйте. Я голосовой секретарь."))
        # The receipt is built from the broker that collected the transcript.
        receipt = worker.transport_receipt(broker, OWNER, "c1")
        assert receipt == {
            "type": "receipt",
            "state": "answered",
            "text": "Здравствуйте. Я голосовой секретарь.",
            "person": OWNER,
            "callId": "c1",
            "turns": 1,
        }
        # A call without spoken text is reported as unanswered.
        empty = worker.transport_receipt(worker._ControlBroker(Inner(), None), OWNER, "c2")
        assert empty["state"] == "unanswered"
        assert empty["turns"] == 0
        assert empty["text"] == ""
        assert empty["callId"] == "c2"

    asyncio.run(check())


def test_a_setup_failure_clears_the_call_member_and_disconnects(monkeypatch):
    """A failure after the membership is published must not leave it behind."""
    import types

    worker = load("matrix_call_answer_worker", "universal_userio.voice.matrix_call_answer_worker")
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")

    class Participant:
        async def publish_track(self, track, options):
            return None

    class Room:
        def __init__(self):
            self.disconnected = False
            self.local_participant = Participant()

        async def connect(self, url, jwt, options):
            return None

        async def disconnect(self):
            self.disconnected = True

        def on(self, event):
            return lambda function: function

    class Source:
        async def wait_for_playout(self):
            return None

        async def aclose(self):
            return None

    class AudioSource:
        def __init__(self, *args, **kwargs):
            pass

    class LocalAudioTrack:
        @staticmethod
        def create_audio_track(name, source):
            return object()

    class TrackPublishOptions:
        def __init__(self, source=None):
            pass

    class TrackSource:
        SOURCE_MICROPHONE = "microphone"

    class RoomOptions:
        def __init__(self, connect_timeout=15):
            pass

    rooms = []

    class TrackedRoom(Room):
        def __init__(self):
            super().__init__()
            rooms.append(self)

    livekit = types.ModuleType("livekit")
    livekit.rtc = types.SimpleNamespace(
        Room=TrackedRoom, AudioSource=AudioSource, LocalAudioTrack=LocalAudioTrack,
        TrackPublishOptions=TrackPublishOptions, TrackSource=TrackSource, RoomOptions=RoomOptions)
    monkeypatch.setitem(sys.modules, "livekit", livekit)

    class BrokenPipeline:
        def __init__(self, config):
            pass

        def synthesize(self, text):
            raise RuntimeError("speech synthesis failed")

    monkeypatch.setattr(worker, "LocalSpeechPipeline", BrokenPipeline)

    requests = []

    def fake_request(server, token, method, path, payload=None):
        requests.append((method, path, payload))
        if path.endswith("/_matrix/client/v3/devices"):
            return {"devices": [{"device_id": "DEV"}]}
        if path.endswith("/state"):
            return [{"type": "m.room.member", "state_key": "@aiadmin:chat.bezrabotnyi.com",
                     "content": {"membership": "join"}},
                    {"type": "m.room.member", "state_key": CALLER,
                     "content": {"membership": "join"}}]
        if path.endswith("/sfu/get"):
            return {"url": "wss://rtc.example", "jwt": "jwt"}
        return {}

    monkeypatch.setattr(worker, "_request", fake_request)

    request = {
        "call": {"room_id": "!room:chat.bezrabotnyi.com", "caller": CALLER,
                 "focus": {"type": "livekit", "livekit_service_url": "https://rtc.example/sfu/get"},
                 "expires_ms": 240000, "max_seconds": 120},
        "account": {"id": "matrix:1", "homeserver": "https://matrix.example",
                    "access_token": "t", "matrix_user_id": "@aiadmin:chat.bezrabotnyi.com"},
        "identity": {"device_id": "DEV"},
        "rtc_auth_url": "https://rtc.example/sfu/get",
    }

    async def check():
        with pytest.raises(RuntimeError):
            await worker.answer_incoming_call(request, bridge.BackendConfig.from_environment(),
                                             broker=None, writer=None,
                                             identities={"matrix:" + CALLER: OWNER})

    asyncio.run(check())
    # The published membership is cleared and the room is disconnected.
    published = [item for item in requests
                 if item[0] == "PUT" and isinstance(item[2], dict) and item[2]]
    cleared = [item for item in requests
               if item[0] == "PUT" and isinstance(item[2], dict) and not item[2]]
    assert published, "the membership was published"
    assert cleared, "the membership was cleared"
    assert rooms and rooms[0].disconnected


def test_the_incident_worker_environment_carries_the_module_path():
    """The MCP servers of the worker import the UserIO voice package."""
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")
    environment = bridge.worker_environment("key")
    assert environment["HOME"] == "/root"
    assert environment["GENERIC_API_KEY"] == "key"
    # The child processes of the worker import the voice package by path.
    assert "PYTHONPATH" in environment
    assert "PATH" in environment and "LANG" in environment
    # The worker is really created with this environment.
    bridge_source = (ROOT / "src/universal_userio/voice/agentcall_realtime_bridge.py").read_text(
        encoding="utf-8")
    assert "environment=worker_environment(api_key)" in bridge_source


def test_every_mcp_server_of_the_worker_keeps_the_module_path():
    """The MCP servers are started with their own environment only."""
    worker = load("agentcall_fast_agent_worker", "universal_userio.voice.agentcall_fast_agent_worker")
    bridge = load("agentcall_realtime_bridge", "universal_userio.voice.agentcall_realtime_bridge")
    import yaml
    settings = yaml.safe_load(
        "mcp:\n  servers:\n    agentcall:\n      command: /bin/true\n      env:\n        HERDER: x\n"
        "    AgentHerder:\n      transport: http\n      url: http://127.0.0.1:18791/mcp\n")
    servers = settings["mcp"]["servers"]
    python_path = bridge.worker_environment("key")["PYTHONPATH"]
    worker.server_environments(servers, python_path)
    # The command servers carry the module path; the http server is untouched.
    assert servers["agentcall"]["env"]["PYTHONPATH"] == python_path
    assert servers["agentcall"]["env"]["HERDER"] == "x"
    assert "env" not in servers["AgentHerder"]
