"""The Matrix callee leg: the person comes from the verified registry, not the request."""
import asyncio
import importlib.util
import json
import sys
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
