"""Fast unit: call receipt ownership, no replay, answer/media gate and PCM framing.

Expected 2 seconds; emergency maximum 20 seconds. These tests do not claim
Telegram recipient acceptance; the native local probe is separately recorded.
"""
import asyncio
import gc
import json
import os
import struct
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from universal_userio.telegram_call_transport import TelegramCallTransport, RecipientUnanswered, encode_bytes, decode_bytes
from universal_userio.telegram_call_worker import AudioSocketWriter, ConnectorRPC


class Native:
    ConnectionState = SimpleNamespace(CONNECTED=1, FAILED=2, TIMEOUT=3, CLOSED=4)
    StreamMode = SimpleNamespace(CAPTURE=1, PLAYBACK=2)
    StreamDevice = SimpleNamespace(MICROPHONE=1, SPEAKER=2)
    MediaSource = SimpleNamespace(EXTERNAL=1)
    AudioDescription = staticmethod(lambda *args: args)
    MediaDescription = staticmethod(lambda *args: args)
    DhConfig = staticmethod(lambda *args: args)
    RTCServer = staticmethod(lambda *args: args)
    FrameData = staticmethod(lambda: None)
    def __init__(self, connect=True):
        self.connect = connect
        self.sources = []; self.sent = []; self.signals = []; self.stops = 0
    def NTgCalls(self): return self
    def on_connection_change(self, callback): self.connection = callback
    def on_signaling_data(self, callback): self.signaling = callback
    def on_frames(self, callback): self.frames = callback
    def get_protocol(self):
        return SimpleNamespace(min_layer=92, max_layer=92, udp_p2p=True,
                               udp_reflector=True, library_versions=["13.0.0"])
    async def create_p2p_call(self, user_id): pass
    async def set_stream_sources(self, user_id, mode, description):
        self.sources.append((mode, description))
    async def init_exchange(self, *args): return b"ga hash"
    async def exchange_keys(self, *args):
        return SimpleNamespace(g_a_or_b=b"ga", key_fingerprint=25)
    async def connect_p2p(self, user_id, *args):
        if self.connect: self.connection(user_id, SimpleNamespace(state=1))
    async def send_signaling_data(self, user_id, data): self.signals.append(data)
    async def send_external_frame(self, user_id, device, pcm, frame_data): self.sent.append(pcm)
    async def stop(self, user_id): self.stops += 1


def setup(native=None, early=None, receipt_target=7):
    native = native or Native()
    operations = []; received = []
    transport = None
    async def rpc(method, params):
        operations.append((method, params))
        if method == "messages.getDhConfig":
            return {"g": 3, "p": encode_bytes(b"prime"), "random": encode_bytes(b"random")}
        if method == "phone.requestCall":
            if early:
                transport.update(early)
            else:
                transport.update({"type": "phoneCallAccepted", "id": "11", "g_b": encode_bytes(b"gb")})
            return {"id": "11", "access_hash": "22", "participant_id": str(receipt_target)}
        if method == "phone.confirmCall":
            return {"id": "11", "participant_id": "7", "key_fingerprint": "25",
                    "protocol": {"library_versions": ["13.0.0"]}, "connections": [], "p2p_allowed": True}
    transport = TelegramCallTransport(7, rpc, received.append, native=native)
    return transport, native, operations, received


def test_answer_gate_native_crypto_and_both_pcm_tracks():
    async def check():
        transport, native, operations, received = setup()
        with pytest.raises(ConnectionError): await transport.send_pcm(b"\x01\x00")
        await transport.dial()
        assert native.sources == [(1, ((1, 48000, 1, "", False), None, None, None)),
                                  (2, ((1, 48000, 1, "", False), None, None, None))]
        await transport.send_pcm(b"\x01\x00" * 480)
        native.frames(7, 1, 1, [SimpleNamespace(data=b"\x02\x00")])
        native.frames(8, 2, 1, [SimpleNamespace(data=b"\x02\x00")])
        native.frames(7, 2, 1, [SimpleNamespace(data=b"\x03\x00")])
        await asyncio.sleep(0)
        assert native.sent == [b"\x01\x00" * 480] and received == [b"\x03\x00"]
        native.signaling(7, b"ciphertext")
        await asyncio.sleep(0); await asyncio.sleep(0)
        assert any(method == "phone.sendSignalingData" for method, _ in operations)
        with pytest.raises(RuntimeError): await transport.dial()
        await transport.close(); await transport.close()
        assert sum(method == "phone.requestCall" for method, _ in operations) == 1
        assert sum(method == "phone.discardCall" for method, _ in operations) == 1
        assert native.stops == 1
    asyncio.run(check())


def test_request_timeout_is_not_replayed():
    async def check():
        native = Native(); operations = []
        async def rpc(method, params):
            operations.append(method)
            if method == "messages.getDhConfig":
                return {"g": 3, "p": encode_bytes(b"p"), "random": encode_bytes(b"r")}
            raise TimeoutError("unknown receipt")
        transport = TelegramCallTransport(7, rpc, lambda pcm: None, native=native)
        with pytest.raises(TimeoutError): await transport.dial()
        with pytest.raises(RuntimeError): await transport.dial()
        await transport.close()
        assert operations.count("phone.requestCall") == 1 and native.stops == 1
    asyncio.run(check())


@pytest.mark.parametrize('stage', ['before', 'dh', 'native'])
def test_stop_during_preparation_never_requests_a_call(stage):
    async def check():
        transport, native, operations, _ = setup()
        if stage == 'before':
            transport.ended.set()
        elif stage == 'dh':
            original = transport.rpc
            async def rpc(method, params):
                result = await original(method, params)
                transport.ended.set()
                return result
            transport.rpc = rpc
        else:
            async def exchange(*args):
                transport.ended.set()
                return b'ga hash'
            native.init_exchange = exchange
        with pytest.raises(ConnectionError, match='stopped before dialing'):
            await transport.dial()
        assert not any(method == 'phone.requestCall' for method, _ in operations)
        if stage == 'before': assert operations == []
        await transport.close()
    asyncio.run(check())


def test_wrong_recipient_receipt_never_discards_a_foreign_call():
    async def check():
        transport, native, operations, received = setup(receipt_target=8)
        with pytest.raises(ValueError): await transport.dial()
        await transport.close()
        assert not any(method == "phone.discardCall" for method, _ in operations)
    asyncio.run(check())


def test_unanswered_invitation_is_distinct_from_media_timeout():
    async def check():
        transport, native, operations, _ = setup()
        original = transport.rpc
        async def rpc(method, params):
            if method == 'phone.requestCall':
                operations.append((method,params))
                return {'id':'11','access_hash':'22','participant_id':'7'}
            return await original(method,params)
        transport.rpc = rpc
        with pytest.raises(RecipientUnanswered): await transport.dial(answer_seconds=.001)
        await transport.close()
        assert native.stops == 1
        assert [m for m,p in operations].count('phone.discardCall') == 1
        assert not any(m=='phone.confirmCall' for m,p in operations)
    asyncio.run(check())


def test_unanswered_worker_reports_normal_end(monkeypatch):
    from universal_userio import telegram_call_worker as worker
    events=[]
    async def unanswered(): raise RecipientUnanswered('recipient did not answer')
    monkeypatch.setattr(worker,'_main',unanswered)
    monkeypatch.setattr(worker,'emit',events.append)
    worker.main()
    assert events==[{'event':'ended','reason':'unanswered'}]


def test_worker_failure_logs_locations_without_provider_details(monkeypatch, capsys):
    from universal_userio import telegram_call_worker as worker
    events = []
    async def broken():
        raise BufferError('private-provider-payload')
    monkeypatch.setattr(worker, '_main', broken)
    monkeypatch.setattr(worker, 'emit', events.append)
    with pytest.raises(SystemExit):
        worker.main()
    output = capsys.readouterr().err
    assert 'BufferError' in output and 'broken' in output
    assert 'private-provider-payload' not in output
    assert events == [{'event':'error', 'reason':'BufferError'}]


def test_early_discard_is_not_mistaken_for_an_answer():
    async def check():
        transport, native, operations, _ = setup(early={"id": "11", "type": "phoneCallDiscarded"})
        with pytest.raises(RecipientUnanswered): await transport.dial()
        await transport.close()
        assert not any(method == "phone.confirmCall" for method, _ in operations)
    asyncio.run(check())


def test_discard_after_request_receipt_is_an_unanswered_invitation():
    async def check():
        transport, native, operations, _ = setup()
        original = transport.rpc
        async def rpc(method, params):
            if method == 'phone.requestCall':
                operations.append((method, params))
                asyncio.get_running_loop().call_soon(transport.update,
                    {'id': '11', 'type': 'phoneCallDiscarded'})
                return {'id': '11', 'access_hash': '22', 'participant_id': '7'}
            return await original(method, params)
        transport.rpc = rpc
        with pytest.raises(RecipientUnanswered): await transport.dial()
        await transport.close()
        assert native.stops == 1
        assert not any(method in {'phone.confirmCall', 'phone.discardCall'} for method, _ in operations)
    asyncio.run(check())


def test_signaling_accepted_without_media_connection_is_not_connected():
    async def check():
        transport, native, _, _ = setup(native=Native(connect=False))
        with pytest.raises(TimeoutError): await transport.dial(connect_seconds=.001)
        assert not transport.connected.is_set()
        await transport.close()
    asyncio.run(check())


def test_pcm_and_update_buffers_are_bounded():
    async def check():
        transport, native, _, _ = setup()
        await transport.dial()
        for payload in (b"a", b"x" * 1922, b"x" * 958, b""):
            with pytest.raises(ValueError): await transport.send_pcm(payload)
        native.frames(7, 2, 1, [SimpleNamespace(data=b"a")])
        await asyncio.sleep(0)
        assert transport.ended.is_set() and isinstance(transport.failure, ValueError)
        await transport.close()
    asyncio.run(check())


def test_audiosocket_fragments_preserve_pcm_and_hangup():
    async def check():
        transport, native, _, _ = setup()
        await transport.dial()
        writer = AudioSocketWriter(transport, 0x16, 0)
        assert writer.sample_rate == 48000
        writer.write(b"\x16\x03"); await writer.drain()
        assert not native.sent
        writer.write(b"\xc0" + b"\x03\x00" * 480 + b"\x00\x00\x00"); await writer.drain()
        assert native.sent == [b"\x03\x00" * 480] and transport.ended.is_set()
        await transport.close()
    asyncio.run(check())


def test_20ms_packet_preserves_both_native_10ms_halves_and_clock():
    # Fast unit<1s/max10s. Pinned native consumes only960B per invocation;
    # it silently dropped the second half of the old1920B input.
    async def check():
        transport, native, _, _ = setup()
        await transport.dial()
        now = [0.0]; sent_at = []
        async def send(*args):
            sent_at.append(now[0]); native.sent.append(args[2]); now[0] += .002
        async def sleep(delay): now[0] += delay + .0005
        native.send_external_frame = send
        first, second = b"\x01\x00" * 480, b"\x02\x00" * 480
        writer = AudioSocketWriter(transport, 0x16, 0)
        writer.write(b"\x16\x07\x80" + first + second)
        with patch('universal_userio.telegram_call_transport.time.monotonic', side_effect=lambda:now[0]), \
             patch('universal_userio.telegram_call_transport.asyncio.sleep', side_effect=sleep):
            await writer.drain()
        assert native.sent == [first, second]
        assert sent_at == [0.0, .0105]
        await transport.close()
    asyncio.run(check())


def test_connector_eof_fails_pending_rpc_without_blocking_a_thread():
    async def check():
        reader = asyncio.StreamReader(); reader.feed_eof()
        rpc = ConnectorRPC(reader)
        transport, _, _, _ = setup(); rpc.transport = transport
        future = asyncio.get_running_loop().create_future(); rpc.pending["1"] = future
        await rpc.read()
        with pytest.raises(ConnectionError): await future
        assert transport.ended.is_set()
        await transport.close()
    asyncio.run(check())


@pytest.mark.skipif(os.environ.get("USERIO_TEST_NATIVE_CALL_LOOPBACK") != "1",
                    reason="focused native integration requires explicit bounded runner")
def test_native_crypto_signaling_bidirectional_pcm_and_cleanup():
    """Focused integration, expected3s/max30s: native crypto/media with no account.

    Uses the standard safe RFC3526 group14 prime only in the local fixture;
    production DH configuration always comes from the existing Telegram client.
    """
    native = pytest.importorskip("ntgcalls")
    prime = bytes.fromhex(
        "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020"
        "BBEA63B139B22514A08798E3404DDEF9519B3CD3A431B302B0A6DF25F14374FE1"
        "356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7EDEE386"
        "BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA483"
        "61C55D39A69163FA8FD24CF5F83655D23DCA3AD961C62F356208552BB9ED529077"
        "096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3BE39E772C180E"
        "86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE"
        "515D2261898FA051015728E5A8AACAA68FFFFFFFFFFFFFFFF")
    async def check():
        loop = asyncio.get_running_loop()
        peer = native.NTgCalls()
        source_received, peer_received, forwarded = [], [], []
        transport = None
        peer_started = False
        async def forward(data):
            await transport.engine.send_signaling_data(7, data)
        def signal(user_id, data):
            # Native methods return asyncio.Future, not coroutine objects.
            # Invoke them inside the owning loop, never the native callback thread.
            forwarded.append(asyncio.run_coroutine_threadsafe(forward(bytes(data)), loop))
        peer.on_signaling_data(signal)
        peer.on_frames(lambda uid, mode, device, frames:
            peer_received.extend(bytes(frame.data) for frame in frames)
            if mode == native.StreamMode.PLAYBACK else None)
        peer.on_connection_change(lambda *args: None)
        await peer.create_p2p_call(8)
        for mode in (native.StreamMode.CAPTURE, native.StreamMode.PLAYBACK):
            await peer.set_stream_sources(8, mode, native.MediaDescription(
                native.AudioDescription(native.MediaSource.EXTERNAL, 48000, 1, "", False),
                None, None, None))
        async def rpc(method, params):
            if method == "messages.getDhConfig":
                return {"g": 2, "p": encode_bytes(prime), "random": encode_bytes(os.urandom(256))}
            if method == "phone.requestCall":
                gb = await peer.init_exchange(8, native.DhConfig(2, prime, os.urandom(256)),
                                              decode_bytes(params["g_a_hash"]))
                transport.update({"type": "phoneCallAccepted", "id": "11", "g_b": encode_bytes(gb)})
                return {"id": "11", "access_hash": "22", "participant_id": "7"}
            if method == "phone.confirmCall":
                await peer.exchange_keys(8, decode_bytes(params["g_a"]), int(params["key_fingerprint"]))
                versions = native.NTgCalls.get_protocol().library_versions
                return {"id": "11", "participant_id": "7", "key_fingerprint": params["key_fingerprint"],
                        "protocol": {"library_versions": versions}, "connections": [], "p2p_allowed": True}
            if method == "phone.sendSignalingData":
                nonlocal peer_started
                # Match the successful direct probe: outgoing engine starts first.
                # Starting the peer during confirmation consumes its ICE timeout
                # before the outgoing engine can produce its first signalling.
                if not peer_started:
                    peer_started = True
                    await peer.connect_p2p(8, [], native.NTgCalls.get_protocol().library_versions, True, None)
                await peer.send_signaling_data(8, decode_bytes(params["data"]))
            return True
        transport = TelegramCallTransport(7, rpc, source_received.append, native=native)
        try:
            await transport.dial(connect_seconds=15)
            import math
            pcm = struct.pack("<480h", *[round(1000 * math.sin(2 * math.pi * 440 * i / 48000)) for i in range(480)])
            for _ in range(60):
                await transport.send_pcm(pcm)
                await peer.send_external_frame(8, native.StreamDevice.MICROPHONE, pcm, native.FrameData())
                await asyncio.sleep(.01)
            await asyncio.sleep(.3)
            assert any(any(frame) for frame in source_received)
            assert any(any(frame) for frame in peer_received)
            assert all(len(frame) == 960 for frame in source_received + peer_received)
        finally:
            peer.on_signaling_data(lambda *args: None)
            peer.on_frames(lambda *args: None)
            peer.on_connection_change(lambda *args: None)
            await asyncio.gather(*(asyncio.wrap_future(future) for future in forwarded),
                                 return_exceptions=True)
            await transport.close()
            await peer.stop(8)
            del peer
            gc.collect()
            await asyncio.sleep(.1)
    asyncio.run(check())


def test_ringing_updates_cannot_abort_a_call_before_answer():
    """Fast unit expected1s/max10s: a64-update provider page must preserve ringing."""
    async def check():
        transport, native, operations, _ = setup()
        for _ in range(64):
            transport.update({'type':'phoneCallWaiting','id':'11','receive_date':1})
        assert not transport.ended.is_set()
        await transport.dial()
        assert transport.connected.is_set()
        assert [m for m,p in operations].count('phone.requestCall') == 1
        await transport.close()
    asyncio.run(check())


def test_native_signaling_burst_waits_for_serial_rpc_without_aborting():
    """Fast unit1s/max10s: recipient handshake burst exceeds old64task cap."""
    async def check():
        transport, native, _, _ = setup()
        await transport.dial()
        blocked = asyncio.Event()
        sent = []
        original = transport.rpc
        async def rpc(method, params):
            if method == 'phone.sendSignalingData':
                await blocked.wait()
                sent.append(decode_bytes(params['data']))
                await asyncio.sleep(0)
                return True
            return await original(method, params)
        transport.rpc = rpc
        payloads = [i.to_bytes(2, 'big') * 100 for i in range(80)]
        for data in payloads:
            native.signaling(7, data)
        await asyncio.sleep(0); await asyncio.sleep(0)
        assert not transport.ended.is_set()
        assert len(transport.tasks) <= 2  # Update pump plus one RPC sender.
        assert transport.pending_signal_bytes == sum(map(len, payloads))
        blocked.set()
        while len(sent) < len(payloads):
            await asyncio.sleep(0)
        assert sent == payloads and not transport.ended.is_set()
        await transport.close()
    asyncio.run(check())


def test_native_signaling_queue_retains_a_finite_byte_budget():
    async def check():
        transport, _, _, _ = setup()
        transport._queue_signaling(b'x' * (transport.signaling_queue_bytes + 1))
        assert isinstance(transport.failure, BufferError)
        assert transport.pending_signal_bytes == 0
        await transport.close()
    asyncio.run(check())


def test_recipient_signaling_burst_survives_native_connection_setup():
    """Fast unit1s/max10s: observed65 packets before media can finish setup."""
    async def check():
        transport, _, _, _ = setup()
        packets = [{'type':'signaling', 'id':'11', 'data':encode_bytes(b'x' * 150)}
                   for _ in range(80)]
        for packet in packets:
            transport.update(packet)
        assert not transport.ended.is_set()
        assert transport.pending_update_bytes == sum(len(json.dumps(
            p, separators=(',', ':'), ensure_ascii=True).encode('ascii')) for p in packets)
        for packet in packets:
            assert await transport._next_update(float('inf')) == packet
        assert transport.pending_update_bytes == 0
        transport.update({'type':'signaling', 'id':'11',
                          'data':'x' * (transport.signaling_queue_bytes + 1)})
        assert isinstance(transport.failure, BufferError)
        await transport.close()
    asyncio.run(check())


def test_slow_recipient_and_media_can_start_beyond_the_previous_deadlines(monkeypatch):
    """Focused integration expected1s/max10s: answer75s +connect35s, virtual time."""
    async def check():
        import time
        transport,native,operations,_=setup()
        async def late_answer(deadline, *, waiting_for_answer=False):
            if not waiting_for_answer:
                await asyncio.Future()
            # The old60s invitation budget rejected this still-ringing peer.
            if deadline - time.monotonic() < 75:
                raise RecipientUnanswered('invitation ended before recipient could answer')
            return {'type':'phoneCallAccepted','id':'11','g_b':encode_bytes(b'gb')}
        monkeypatch.setattr(transport,'_next_update',late_answer)
        wait=asyncio.wait
        async def slow_media(tasks, *, timeout, return_when):
            # The old20s media window closed while a slow client was attaching.
            if timeout < 35:
                return set(),set(tasks)
            return await wait(tasks,timeout=timeout,return_when=return_when)
        monkeypatch.setattr(asyncio,'wait',slow_media)
        await transport.dial()
        assert transport.connected.is_set() and not transport.ended.is_set()
        assert [m for m,p in operations].count('phone.requestCall')==1
        await transport.close()
    asyncio.run(check())
