"""An encrypted room must never fall through to unencrypted RTC media."""
import asyncio
import sys
import types
import uuid

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
