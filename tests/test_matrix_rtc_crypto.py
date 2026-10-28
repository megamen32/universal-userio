"""Call-bound crypto control, real LiveKit option compatibility and lifecycle."""
import asyncio
import base64
import json
import types
import importlib.util
import io
from pathlib import Path

import pytest

from universal_userio.matrix_rtc_crypto import MediaEncryption, CryptoControl


def request():
    return {"account": {"matrix_user_id": "@b:example.org"},
            "call": {"caller": "@a:example.org", "room_id": "!qa:example.org",
                     "caller_device": "A", "membership_id": "call-one"}}


def result(membership="call-one", index=0):
    return {"device_id": "B", "members": [{"identity": "@a:example.org:A", "membership_id": membership}],
            "keys": [{"identity": "@a:example.org:A", "device_id": "A", "index": index,
                      "membership_id": membership, "key": base64.b64encode(b"r"*16).decode()}]}


def test_encrypted_join_fails_closed_without_the_bound_control_reader():
    with pytest.raises(ValueError, match="key exchange unavailable"):
        MediaEncryption(request(), None, None)


def test_livekit_options_match_element_hkdf_and_sender_indices():
    rtc = pytest.importorskip("livekit.rtc")
    options = MediaEncryption(request(), object(), object()).options(rtc)
    assert options.e2ee.key_provider_options.shared_key is None
    assert options.e2ee.key_provider_options.key_derivation_function == 1  # HKDF
    assert options.e2ee.key_provider_options.key_ring_size == 256
    assert options.e2ee.key_provider_options.ratchet_window_size == 10


def test_membership_change_is_rejected_before_media_connect():
    async def check():
        crypto = MediaEncryption(request(), object(), object())
        async def rpc(**kwargs): return result("different-call")
        crypto.rpc = rpc
        with pytest.raises(PermissionError, match="membership changed"):
            await crypto.prepare()
    asyncio.run(check())


def test_control_responses_are_correlated_and_cannot_inject_another_call():
    async def check():
        reader = asyncio.StreamReader()
        class Writer:
            def write(self, data):
                event = json.loads(data)
                reader.feed_data((json.dumps({"type": "matrix_crypto_result", "request_id": "wrong",
                                              "result": result()})+"\n").encode())
            async def drain(self): pass
        crypto = MediaEncryption(request(), reader, Writer())
        with pytest.raises(RuntimeError, match="control failed"):
            await crypto.prepare()
    asyncio.run(check())


def test_keys_rotate_before_switching_native_cryptor_and_cleanup_stops_polling():
    async def check():
        crypto = MediaEncryption(request(), object(), object())
        crypto.POLL_SECONDS = .001
        crypto.USE_KEY_DELAY = 0
        installed, indices, sent = [], [], []
        rotated = asyncio.Event()
        room = types.SimpleNamespace(local_participant=types.SimpleNamespace(identity="@b:example.org:B"))
        class Provider:
            def set_key(self, identity, key, index):
                assert len(key) == 16
                installed.append((identity, index))
                if identity == "@b:example.org:B" and index == 1:
                    assert sent[-1] == 1  # encrypted key send precedes use
                    rotated.set()
        class Cryptor:
            participant_identity = "@b:example.org:B"
            def set_key_index(self, index): indices.append(index)
        room.e2ee_manager = types.SimpleNamespace(key_provider=Provider(), frame_cryptors=lambda: [Cryptor()])
        async def rpc(*, key=None):
            if key:
                sent.append(crypto.index)
            return result("call-one" if not sent else "call-two", 0 if not sent else 1)
        crypto.rpc = rpc
        await crypto.prepare()
        await crypto.start(room, lambda: None)
        # The peer's new membership triggers sender rotation on the next poll.
        crypto.members = result()["members"]
        await asyncio.wait_for(rotated.wait(), 1)
        assert indices[-1] == 1
        await crypto.close()
        assert crypto.task.done()
        assert not any(crypto.sender_key)
        assert crypto.keys == [] and crypto.members == []
    asyncio.run(check())


def test_manual_encrypted_caller_accepts_only_the_bound_fixture_private_input(monkeypatch):
    path = Path(__file__).parent / "manual" / "matrix_rtc_ai_caller.py"
    spec = importlib.util.spec_from_file_location("matrix_fixture_crypto_input", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    account = {"user_id": "fixture-principal", "account_id": "matrix:ab023a689e926bf99d924e933ebd",
               "matrix_user_id": "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"}
    payload = {"account": account, "call": {"room_id": module.ROOM, "target": module.TARGET}}
    monkeypatch.setenv("CALLER_TRANSPORT_INPUT", "private-jsonl")
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(payload)+"\n"))
    assert module._caller_credentials() == ("fixture-principal", account["account_id"], account)
    payload["account"]["account_id"] = "matrix:personal"
    monkeypatch.setattr(module.sys, "stdin", io.StringIO(json.dumps(payload)+"\n"))
    with pytest.raises(PermissionError, match="binding_mismatch"):
        module._caller_credentials()
