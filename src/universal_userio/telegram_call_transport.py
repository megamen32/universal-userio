"""Native Telegram P2P media; signalling stays on the existing connector.

NTgCalls owns DH validation, key exchange, encrypted signalling and WebRTC.
This module never constructs a Telegram client or handles login credentials.
The caller supplies an account-bound RPC port and forwards that client's
phone updates. One instance admits exactly one outgoing call, without retries.
"""
from __future__ import annotations

import asyncio
import base64
import secrets
import time
from typing import Any, Awaitable, Callable


def encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value: str) -> bytes:
    return base64.b64decode(value, validate=True)


class RecipientUnanswered(TimeoutError):
    """Invitation expired before acceptance; not a media/provider failure."""


class TelegramCallTransport:
    """48 kHz mono signed little-endian PCM, matching AgentCall AudioSocket.

    ``rpc(method, params)`` returns normalized JSON (see the transport guide).
    It must use an already authorized client, a fixed caller and fixed recipient.
    ``receive_pcm`` runs on the owning asyncio loop; it must remain bounded.
    """

    sample_rate = 48000
    frame_bytes = sample_rate * 2 // 100  # NTgCalls consumes exactly 10ms.
    queue_bytes = sample_rate * 2 * 2    # At most two seconds in callbacks.

    def __init__(self, user_id: int, rpc: Callable[..., Awaitable[Any]],
                 receive_pcm: Callable[[bytes], None], *, native=None):
        if isinstance(user_id, bool) or user_id <= 0:
            raise ValueError("a verified private recipient ID is required")
        if native is None:
            import ntgcalls as native
        self.module = native
        self.engine = native.NTgCalls()
        self.user_id, self.rpc, self.receive_pcm = user_id, rpc, receive_pcm
        self.loop = asyncio.get_running_loop()
        self.call: dict | None = None
        self.attempted = False
        self.created = False
        self.closed = False
        self.discarded = False
        self.connected = asyncio.Event()
        self.ended = asyncio.Event()
        self.failure: BaseException | None = None
        self.updates: asyncio.Queue = asyncio.Queue(maxsize=32)
        self.tasks: set[asyncio.Task] = set()
        self.pending_pcm_bytes = 0
        self.started_at: float | None = None
        self.engine.on_connection_change(self._connection)
        self.engine.on_signaling_data(self._signaling)
        self.engine.on_frames(self._frames)

    def _fail(self, error: BaseException):
        self.failure = error
        self.ended.set()

    def _spawn(self, coroutine):
        if self.closed:
            coroutine.close()
            return
        if len(self.tasks) >= 64:
            coroutine.close()
            self._fail(BufferError("Telegram signalling tasks exceeded"))
            return
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception():
                self._fail(done.exception())
        task.add_done_callback(finished)

    def _connection(self, user_id, info):
        def deliver():
            if user_id != self.user_id or self.closed:
                return
            state = info.state
            if state == self.module.ConnectionState.CONNECTED:
                self.started_at = self.started_at or time.monotonic()
                self.connected.set()
            elif state in (self.module.ConnectionState.FAILED,
                           self.module.ConnectionState.TIMEOUT):
                self._fail(ConnectionError("Telegram media connection failed"))
            elif state == self.module.ConnectionState.CLOSED:
                self.ended.set()
        self.loop.call_soon_threadsafe(deliver)

    def _signaling(self, user_id, data):
        if user_id == self.user_id and not self.closed:
            self.loop.call_soon_threadsafe(self._spawn, self._send_signaling(bytes(data)))

    async def _send_signaling(self, data):
        if self.call is None:
            raise RuntimeError("media signalling preceded the call receipt")
        await self.rpc("phone.sendSignalingData", {
            "peer": self._peer(), "data": encode_bytes(data)})

    def _frames(self, user_id, mode, device, frames):
        if self.closed or user_id != self.user_id or mode != self.module.StreamMode.PLAYBACK:
            return
        if device not in (self.module.StreamDevice.MICROPHONE,
                          self.module.StreamDevice.SPEAKER):
            return
        # Native frames are copied before leaving the native callback thread.
        payloads = [bytes(frame.data) for frame in frames]
        size = sum(map(len, payloads))
        if size > self.queue_bytes or self.pending_pcm_bytes + size > self.queue_bytes:
            self.loop.call_soon_threadsafe(self._fail, BufferError("inbound PCM queue exceeded"))
            return
        self.pending_pcm_bytes += size
        def deliver():
            self.pending_pcm_bytes -= size
            if self.closed or not self.connected.is_set() or self.ended.is_set():
                return
            for pcm in payloads:
                if len(pcm) > self.queue_bytes or len(pcm) % 2:
                    self._fail(ValueError("invalid inbound PCM frame"))
                    return
                try:
                    self.receive_pcm(pcm)
                except Exception as error:
                    self._fail(error)
                    return
        self.loop.call_soon_threadsafe(deliver)

    def update(self, update: dict):
        """Forward normalized updates from the fixed existing caller client."""
        if self.closed:
            return
        if self.call and str(update.get("id")) != str(self.call["id"]):
            return
        if update.get("type") == "phoneCallDiscarded" and self.call:
            self.discarded = True
            self.ended.set()
        try:
            self.updates.put_nowait(update)
        except asyncio.QueueFull:
            self._fail(BufferError("Telegram update buffer exceeded"))

    def _peer(self):
        return {"id": str(self.call["id"]), "access_hash": str(self.call["access_hash"])}

    def _protocol(self):
        protocol = self.engine.get_protocol()
        return {key: getattr(protocol, key) for key in
                ("min_layer", "max_layer", "udp_p2p", "udp_reflector", "library_versions")}

    def _audio(self):
        return self.module.AudioDescription(self.module.MediaSource.EXTERNAL,
                                            self.sample_rate, 1, "", False)

    async def _next_update(self, deadline, *, waiting_for_answer=False):
        get = asyncio.create_task(self.updates.get())
        end = asyncio.create_task(self.ended.wait())
        try:
            done, _ = await asyncio.wait((get, end),
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED)
            if self.ended.is_set():
                if waiting_for_answer and self.discarded and self.failure is None:
                    raise RecipientUnanswered("Telegram invitation ended without an answer")
                raise self.failure or ConnectionError("Telegram call ended")
            if get not in done:
                raise RecipientUnanswered("Telegram recipient did not answer")
            return get.result()
        finally:
            get.cancel(); end.cancel()
            await asyncio.gather(get, end, return_exceptions=True)

    async def dial(self, *, answer_seconds=60, connect_seconds=20):
        if self.attempted:
            raise RuntimeError("this outgoing call was already attempted; reconcile its receipt")
        def check_stopped():
            if self.closed or self.ended.is_set():
                raise ConnectionError("Telegram call stopped before dialing")
        check_stopped()
        self.attempted = True
        dh = await self.rpc("messages.getDhConfig", {"version": 0, "random_length": 256})
        check_stopped()
        await self.engine.create_p2p_call(self.user_id)
        self.created = True
        await self.engine.set_stream_sources(self.user_id, self.module.StreamMode.CAPTURE,
            self.module.MediaDescription(self._audio(), None, None, None))
        g_a_hash = await self.engine.init_exchange(self.user_id,
            self.module.DhConfig(int(dh["g"]), decode_bytes(dh["p"]), decode_bytes(dh["random"])), None)
        check_stopped()
        receipt = await self.rpc("phone.requestCall", {
            "user_id": str(self.user_id), "random_id": secrets.randbelow(2**31),
            "g_a_hash": encode_bytes(g_a_hash), "protocol": self._protocol(), "video": False})
        if int(receipt["id"]) <= 0 or int(receipt["participant_id"]) != self.user_id:
            raise ValueError("Telegram request receipt belongs to another recipient")
        self.call = receipt
        deadline = time.monotonic() + answer_seconds
        while True:
            update = await self._next_update(deadline, waiting_for_answer=True)
            if str(update.get("id")) != str(self.call["id"]):
                continue
            if update.get("type") == "phoneCallDiscarded":
                self.discarded = True
                self.ended.set()
                raise RecipientUnanswered("Telegram invitation ended without an answer")
            if update.get("type") == "signaling":
                await self.engine.send_signaling_data(self.user_id, decode_bytes(update["data"]))
            elif update.get("type") == "phoneCallAccepted":
                break
        auth = await self.engine.exchange_keys(self.user_id, decode_bytes(update["g_b"]), 0)
        confirmed = await self.rpc("phone.confirmCall", {
            "peer": self._peer(), "g_a": encode_bytes(auth.g_a_or_b),
            "key_fingerprint": str(auth.key_fingerprint), "protocol": self._protocol()})
        if (str(confirmed["id"]) != str(self.call["id"])
                or int(confirmed["participant_id"]) != self.user_id
                or int(confirmed["key_fingerprint"]) != auth.key_fingerprint):
            raise ValueError("Telegram confirmation belongs to another call")
        servers = [self.module.RTCServer(int(item["id"]), item["ip"], item.get("ipv6", ""),
            int(item["port"]), item.get("username"), item.get("password"),
            bool(item.get("turn")), bool(item.get("stun")), bool(item.get("tcp")),
            decode_bytes(item["peer_tag"]) if item.get("peer_tag") else None)
            for item in confirmed["connections"]]
        await self.engine.set_stream_sources(self.user_id, self.module.StreamMode.PLAYBACK,
            self.module.MediaDescription(self._audio(), None, None, None))
        self._spawn(self._pump_updates())
        await self.engine.connect_p2p(self.user_id, servers,
            confirmed["protocol"]["library_versions"], bool(confirmed.get("p2p_allowed")),
            confirmed.get("custom_parameters"))
        ready = asyncio.create_task(self.connected.wait())
        end = asyncio.create_task(self.ended.wait())
        try:
            done, _ = await asyncio.wait((ready, end), timeout=connect_seconds,
                                        return_when=asyncio.FIRST_COMPLETED)
            if self.ended.is_set():
                raise self.failure or ConnectionError("Telegram media ended before connection")
            if ready not in done:
                raise TimeoutError("Telegram media did not connect")
        finally:
            ready.cancel(); end.cancel()
            await asyncio.gather(ready, end, return_exceptions=True)

    async def _pump_updates(self):
        while not self.ended.is_set():
            update = await self._next_update(float("inf"))
            if update.get("type") == "signaling":
                await self.engine.send_signaling_data(self.user_id, decode_bytes(update["data"]))

    async def send_pcm(self, pcm: bytes):
        if not self.connected.is_set() or self.ended.is_set() or self.closed:
            raise ConnectionError("Telegram media is unavailable")
        # Native AudioStreamer ignores the supplied size and reads a fixed
        # 10ms block. Short blocks are unsafe; 20ms blocks lose their tail.
        if len(pcm) not in (self.frame_bytes, self.frame_bytes * 2):
            raise ValueError("PCM must contain one or two complete 10ms frames")
        start = time.monotonic()
        for offset in range(0, len(pcm), self.frame_bytes):
            if offset:
                await asyncio.sleep(max(0, start + .01 - time.monotonic()))
            if self.ended.is_set() or self.closed:
                raise ConnectionError("Telegram media ended during playback")
            await self.engine.send_external_frame(self.user_id, self.module.StreamDevice.MICROPHONE,
                pcm[offset:offset + self.frame_bytes], self.module.FrameData())

    async def close(self):
        if self.closed:
            return
        self.closed = True
        self.ended.set()
        # Release native→bound-method→transport cycles while its asyncio loop
        # still exists; late callbacks must not touch a closed Python loop.
        self.engine.on_signaling_data(lambda *args: None)
        self.engine.on_frames(lambda *args: None)
        self.engine.on_connection_change(lambda *args: None)
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        try:
            if self.call and not self.discarded:
                await self.rpc("phone.discardCall", {
                    "peer": self._peer(), "duration": int(time.monotonic() - self.started_at)
                        if self.started_at else 0,
                    "reason": "hangup" if self.started_at else "missed",
                    "connection_id": "0", "video": False})
        finally:
            if self.created:
                await self.engine.stop(self.user_id)
            self.engine = None
