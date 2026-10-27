"""Inbound Matrix call intake: admission, durable dedup, scope and dispatch."""
import importlib.util
import json
import time
from pathlib import Path

import pytest


def load():
    import sys
    path = Path(__file__).resolve().parents[1] / "src/universal_userio/matrix_inbound_calls.py"
    spec = importlib.util.spec_from_file_location("matrix_inbound_calls", path)
    module = importlib.util.module_from_spec(spec)
    # A dataclass with slots resolves its module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OWN = "@aiadmin:chat.bezrabotnyi.com"
CALLER = "@bezrabotnyi:chat.bezrabotnyi.com"
QA_CALLER = "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"
QA_CALLEE = "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"
ROOM = "!room:chat.bezrabotnyi.com"


def call(module, **overrides):
    values = dict(room_id=ROOM, event_id="$ring1", caller=CALLER, call_id="",
                  focus={"type": "livekit", "livekit_service_url": "https://rtc.example/sfu/get"},
                  expires_ms=240000, origin_server_ts=int(time.time() * 1000),
                  joined=(OWN, CALLER))
    values.update(overrides)
    return module.InboundMatrixCall(**values)


def test_only_an_allowed_account_may_be_answered():
    module = load()
    assert module.admit_call(call(module), own_user_id=OWN, allowed_accounts=(OWN,)) is None
    other = module.admit_call(call(module), own_user_id=QA_CALLEE, allowed_accounts=(OWN,))
    assert other == "account_not_allowed"


def test_the_caller_must_be_trusted_and_the_room_one_to_one():
    module = load()
    assert module.admit_call(call(module, caller=""), own_user_id=OWN, allowed_accounts=(OWN,)) == "caller_not_trusted"
    assert module.admit_call(call(module, caller=OWN), own_user_id=OWN, allowed_accounts=(OWN,)) == "caller_not_trusted"
    crowded = module.admit_call(call(module, joined=(OWN, CALLER, "@third:chat.bezrabotnyi.com")),
                               own_user_id=OWN, allowed_accounts=(OWN,))
    assert crowded == "room_not_one_to_one"


def test_an_expired_call_is_never_answered():
    module = load()
    expired = call(module, expires_ms=1000, origin_server_ts=int(time.time() * 1000))
    reason = module.admit_call(expired, own_user_id=OWN, allowed_accounts=(OWN,),
                               now=time.time() + 10)
    assert reason == "call_expired"


def test_the_qa_pair_uses_the_same_handler_without_an_owner_identity():
    module = load()
    qa = call(module, caller=QA_CALLER, joined=tuple(sorted((QA_CALLEE, QA_CALLER))))
    assert module.admit_call(qa, own_user_id=QA_CALLEE, allowed_accounts=(QA_CALLEE,)) is None


def test_a_repeated_ring_never_answers_the_same_call_twice(tmp_path):
    module = load()
    root = tmp_path / "calls"
    receipt_id = call(module).receipt_id("matrix:1")
    module.write_receipt(root, receipt_id, {"call_id": receipt_id, "status": "sent"})
    assert module.load_receipt(root, receipt_id) is not None
    assert module.load_receipt(root, str(__import__("uuid").uuid4())) is None


def test_the_answer_request_carries_no_person_and_only_the_trusted_caller():
    module = load()
    request = module.build_answer_request(
        call(module),
        account={"id": "matrix:1", "matrix_user_id": OWN},
        secret={"homeserver": "https://matrix.example", "access_token": "t", "matrix_user_id": OWN},
        identity={"device_id": "D"},
        rtc_auth_url="https://rtc.example/sfu/get")
    assert request["action"] == module.CALLEE_ACTION
    assert request["call"]["caller"] == CALLER
    assert request["call"]["room_id"] == ROOM
    # The person identity is resolved by the voice host, never by the request.
    assert "person" not in request["call"]
    assert "person" not in request


def test_the_control_route_must_be_the_private_voice_route(tmp_path):
    module = load()
    path = tmp_path / "matrix-call-control.json"
    path.write_text(json.dumps({"socket_path": "/run/agentcall/primary88/control.sock",
                                "execution_host": "roomhacker-server-88"}))
    path.chmod(0o600)
    assert module.control_socket_path(path) == "/run/agentcall/primary88/control.sock"
    path.write_text(json.dumps({"socket_path": "/run/agentcall/primary88/control.sock",
                                "execution_host": "somewhere-else"}))
    with pytest.raises(ValueError):
        module.control_socket_path(path)
    path.write_text(json.dumps({"socket_path": "/run/agentcall/primary88/control.sock",
                                "execution_host": "roomhacker-server-88"}))
    path.chmod(0o644)
    with pytest.raises(PermissionError):
        module.control_socket_path(path)


def test_the_worker_answers_an_admitted_call_once_and_keeps_a_durable_receipt(tmp_path):
    module = load()
    root = tmp_path / "calls"
    sent = []

    class Store:
        class _Connection:
            def execute(self, query):
                class Rows(list):
                    def __iter__(self):
                        return iter([{"user_id": "u", "id": "matrix:1", "provider": "matrix",
                                      "credential_ref": "connector:abc"}])
                return Rows()
        _lock = __import__("threading").RLock()
        _connection = _Connection()

    class Service:
        _store = Store()

    class Reader:
        def __init__(self, account, secret):
            self.account = account

        def poll(self, cursor, limit=50):
            return [call(module)], "next"

    def answers(request):
        sent.append(request)
        return {"type": "receipt", "state": "completed", "text": "Здравствуйте."}

    class Vault:
        def load(self, credential_ref, *, user_id, provider):
            return {"homeserver": "https://matrix.example", "access_token": "t",
                    "matrix_user_id": OWN, "device_id": "D"}

    worker = module.MatrixInboundCallWorker(
        Service(), allowed_accounts=(OWN,), rtc_auth_url="https://rtc.example/sfu/get",
        call_root=root, reader_factory=lambda account, secret: Reader(account, secret),
        answers=answers, vault=Vault())
    worker._reader = lambda account, secret: Reader(account, secret)
    assert worker.poll_once() == 1
    assert len(sent) == 1
    assert sent[0]["call"]["caller"] == CALLER
    receipt_id = call(module).receipt_id("matrix:1")
    assert module.load_receipt(root, receipt_id)["status"] == "sent"
    # A keepalive or a repeated sync must not answer the same call again.
    assert worker.poll_once() == 0
    assert len(sent) == 1


def _sync_payload(room_id, caller, own):
    """A realistic /sync answer: the room id is in the envelope, not the event."""
    return {
        "next_batch": "s2",
        "rooms": {"join": {room_id: {
            "timeline": {"events": [
                {"type": "org.matrix.msc3401.call.member", "event_id": "$m1",
                 "state_key": "_" + caller + "_DEV_m.call", "sender": caller,
                 "content": {"call_id": "mcall1", "expires": 240000,
                             "foci_preferred": [{"type": "livekit",
                                                 "livekit_service_url": "https://rtc.example/sfu/get"}]}},
                {"type": "org.matrix.msc4075.rtc.notification", "event_id": "$r1",
                 "sender": caller, "origin_server_ts": 1700000000000,
                 "content": {"m.mentions": {"user_ids": [own]}}},
            ]},
            "state": {"events": [
                {"type": "m.room.member", "state_key": own, "sender": own,
                 "content": {"membership": "join"}},
                {"type": "m.room.member", "state_key": caller, "sender": caller,
                 "content": {"membership": "join"}},
            ]},
        }}},
    }


def test_the_reader_uses_the_room_envelope_and_the_callers_own_membership():
    module = load()
    payload = _sync_payload(ROOM, CALLER, OWN)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def runner(request, timeout):
        return Response()

    reader = module.MatrixCallReader("https://matrix.example", "t", OWN, runner=runner)
    calls, nxt = reader.poll("s1")
    assert nxt == "s2"
    assert len(calls) == 1
    call = calls[0]
    assert call.room_id == ROOM
    assert call.caller == CALLER
    assert call.joined == tuple(sorted((OWN, CALLER)))
    assert call.focus["livekit_service_url"] == "https://rtc.example/sfu/get"


def test_the_reader_falls_back_to_the_persistent_room_members():
    module = load()
    payload = _sync_payload(ROOM, CALLER, OWN)
    payload["rooms"]["join"][ROOM].pop("state")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def runner(request, timeout):
        if "joined_members" in request.full_url:
            return _MembersResponse()
        return Response()

    class _MembersResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"joined": {OWN: {}, CALLER: {}}}).encode()

    reader = module.MatrixCallReader("https://matrix.example", "t", OWN, runner=runner)
    calls, _nxt = reader.poll("s1")
    assert len(calls) == 1
    assert calls[0].joined == tuple(sorted((OWN, CALLER)))


def test_the_receipt_identity_survives_a_repeated_notification():
    module = load()
    first = call(module, event_id="$ring1", caller_device="DEV")
    second = call(module, event_id="$ring2", caller_device="DEV")
    assert first.receipt_id("matrix:1") == second.receipt_id("matrix:1")
    other = call(module, event_id="$ring3", caller_device="DEV2")
    assert other.receipt_id("matrix:1") != first.receipt_id("matrix:1")


def test_the_claim_is_written_before_the_external_answer(tmp_path):
    module = load()
    root = tmp_path / "calls"
    seen = []

    class Store:
        class _Connection:
            def execute(self, query):
                class Rows(list):
                    def __iter__(self):
                        return iter([{"user_id": "u", "id": "matrix:1", "provider": "matrix",
                                      "credential_ref": "connector:abc"}])
                return Rows()
        _lock = __import__("threading").RLock()
        _connection = _Connection()

    class Service:
        _store = Store()

    class Vault:
        def load(self, credential_ref, *, user_id, provider):
            return {"homeserver": "https://matrix.example", "access_token": "t",
                    "matrix_user_id": OWN, "device_id": "D"}

    class Reader:
        def __init__(self, account, secret):
            pass

        def poll(self, cursor, limit=50):
            return [call(module)], "next"

    def answers(request):
        # The claim must already be on disk while the answer is running.
        receipt_id = call(module).receipt_id("matrix:1")
        seen.append(module.load_receipt(root, receipt_id))
        return {"type": "receipt", "state": "completed", "text": "Здравствуйте."}

    worker = module.MatrixInboundCallWorker(
        Service(), allowed_accounts=(OWN,), rtc_auth_url="https://rtc.example/sfu/get",
        call_root=root, reader_factory=lambda account, secret: Reader(account, secret),
        answers=answers, vault=Vault())
    assert worker.poll_once() == 1
    assert seen and seen[0] is not None
    assert seen[0]["status"] == "accepted"
