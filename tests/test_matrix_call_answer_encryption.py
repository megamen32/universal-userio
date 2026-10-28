"""An encrypted room must never fall through to unencrypted RTC media."""
import asyncio
import sys
import types
import uuid
import json
import base64

import pytest

from universal_userio.voice import matrix_call_answer_worker as worker


@pytest.mark.parametrize("encrypted", [True, False])
def test_incoming_crypto_preflight_precedes_sfu_and_membership(monkeypatch, encrypted):
    caller, callee = "@test_a:example.org", "@test_b:example.org"
    requests = []

    class ReachedOpenId(Exception):
        pass

    def request(server, token, method, path, payload=None):
        requests.append((method, path))
        if path.endswith("/state"):
            state = [{"type": "m.room.member", "state_key": member,
                      "content": {"membership": "join"}} for member in (caller, callee)]
            if encrypted:
                state.append({"type": "m.room.encryption", "state_key": "",
                              "content": {"algorithm": "m.megolm.v1.aes-sha2"}})
            return state
        raise ReachedOpenId("unencrypted join reached OpenID")

    # No native media object is needed before the crypto preflight.
    monkeypatch.setitem(sys.modules, "livekit", types.SimpleNamespace(rtc=object()))
    monkeypatch.setattr(worker, "_request", request)
    config = types.SimpleNamespace(tts_provider="qwen-warm")
    payload = {
        "call": {"room_id": "!isolated:example.org", "caller": caller,
                 "focus": {"type": "livekit", "livekit_service_url": "https://rtc.example.org"}},
        "account": {"matrix_user_id": callee, "homeserver": "https://example.org",
                    "access_token": "test-only"},
        "identity": {"device_id": "CALLEE"}, "rtc_auth_url": "https://rtc.example.org",
    }

    async def check():
        error = ValueError if encrypted else ReachedOpenId
        match = "encrypted MatrixRTC key exchange unavailable" if encrypted else "reached OpenID"
        with pytest.raises(error, match=match):
            await worker.answer_incoming_call(payload, config, broker=None, writer=None,
                                             identities={"matrix:" + caller: str(uuid.uuid4())})

    # Keep the process's default loop untouched for the synchronous voice tests.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(check())
    finally:
        loop.close()
    if encrypted:
        assert len(requests) == 1
        assert requests[0][0] == "GET" and requests[0][1].endswith("/state")


def test_encrypted_incoming_setup_installs_keys_before_publishing_and_cleans_up(monkeypatch):
    from universal_userio.matrix_rtc_crypto import MediaEncryption
    rtc = pytest.importorskip("livekit.rtc")
    caller, callee = "@a:example.org", "@b:example.org"
    steps = []
    state = [{"type": "m.room.member", "state_key": member,
              "content": {"membership": "join"}} for member in (caller, callee)]
    state.append({"type": "m.room.encryption", "content": {"algorithm": "m.megolm.v1.aes-sha2"}})
    reader = None

    class Writer:
        def write(self, data):
            frame = json.loads(data)
            if frame["op"] == "send": steps.append("encrypted-key-send")
            reader.feed_data((json.dumps({"type": "matrix_crypto_result", "request_id": frame["request_id"],
                "result": {"device_id": "B", "members": [{"identity": caller+":A", "membership_id": "m1"}],
                           "keys": [{"identity": caller+":A", "membership_id": "m1", "index": 0,
                                     "key": base64.b64encode(b"x"*16).decode()}]}})+"\n").encode())
        async def drain(self): pass

    class Room:
        def __init__(self):
            self.local_participant = types.SimpleNamespace(identity=callee+":B", publish_track=self.publish)
            self.e2ee_manager = types.SimpleNamespace(key_provider=self, frame_cryptors=lambda: [])
        def set_key(self, identity, key, index): steps.append("install:"+identity)
        async def publish(self, track, options):
            assert "install:"+callee+":B" in steps
            assert "install:"+caller+":A" in steps
            assert "encrypted-key-send" in steps
            steps.append("audio-published")
        async def connect(self, url, jwt, options):
            assert options.e2ee.key_provider_options.key_derivation_function == 1
            steps.append("encrypted-connect")
        async def disconnect(self): steps.append("disconnect")
        def on(self, event): return lambda callback: callback
    class Source:
        def __init__(self, *args, **kwargs): pass
        async def aclose(self): steps.append("source-close")
    monkeypatch.setitem(sys.modules, "livekit", types.SimpleNamespace(rtc=types.SimpleNamespace(
        Room=Room, AudioSource=Source, RoomOptions=rtc.RoomOptions, E2EEOptions=rtc.E2EEOptions,
        KeyProviderOptions=rtc.KeyProviderOptions,
        LocalAudioTrack=types.SimpleNamespace(create_audio_track=lambda *args: object()),
        TrackPublishOptions=lambda **kwargs: object(), TrackSource=rtc.TrackSource)))

    def api(server, token, method, path, payload=None):
        if path.endswith("/state"): return state
        if path.endswith("/sfu/get"): return {"url": "wss://example.org", "jwt": "test"}
        if method == "PUT": steps.append("member-clear" if not payload else "member-publish")
        return {}
    monkeypatch.setattr(worker, "_request", api)
    class BrokenPipeline:
        def __init__(self, config): pass
        def synthesize(self, text): raise RuntimeError("stop after encrypted setup")
    monkeypatch.setattr(worker, "LocalSpeechPipeline", BrokenPipeline)
    payload = {"account": {"matrix_user_id": callee, "homeserver": "https://example.org", "access_token": "test"},
               "identity": {"device_id": "B"}, "rtc_auth_url": "https://rtc.example.org",
               "call": {"room_id": "!qa:example.org", "caller": caller, "caller_device": "A",
                        "membership_id": "m1", "focus": {"livekit_service_url": "https://rtc.example.org"}}}
    async def check():
        nonlocal reader
        reader = asyncio.StreamReader()
        with pytest.raises(RuntimeError, match="stop after encrypted setup"):
            await worker.answer_incoming_call(payload, types.SimpleNamespace(tts_provider="qwen-warm", attention_prompt="test"),
                broker=None, writer=Writer(), control_reader=reader,
                identities={"matrix:"+caller: str(uuid.uuid4())})
    loop = asyncio.new_event_loop()
    try: loop.run_until_complete(check())
    finally: loop.close()
    assert steps.index("encrypted-connect") < steps.index("member-publish") < steps.index("audio-published")
    assert steps[-3:] == ["member-clear", "disconnect", "source-close"]
