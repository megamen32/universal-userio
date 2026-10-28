"""Private call-bound media-key RPC and LiveKit E2EE (no Matrix sync on voice)."""
from __future__ import annotations

import asyncio
import base64
import json
import secrets
import uuid


class CryptoControl:
    """API-side handler: account/room/peer come only from the admitted call."""
    def __init__(self, request):
        from .matrix_crypto import for_account
        self.account = request["account"]
        call = request["call"]
        self.room = call["room_id"]
        self.peer = call.get("caller") or call["target"]
        self.crypto = for_account(self.account)

    def handle(self, event):
        request_id = str(uuid.UUID(event["request_id"]))
        if event.get("op") not in {"poll", "send"}:
            raise ValueError("unsupported Matrix crypto operation")
        result = self.crypto.exchange(self.room, self.peer,
            key=event.get("key") if event["op"] == "send" else None,
            index=event.get("index"), transaction_id=request_id)
        return {"type": "matrix_crypto_result", "request_id": request_id, "result": result}


class MediaEncryption:
    POLL_SECONDS = .5
    USE_KEY_DELAY = 5
    def __init__(self, request, reader, writer):
        if reader is None:
            raise ValueError("encrypted MatrixRTC key exchange unavailable; room was not downgraded")
        self.request, self.reader, self.writer = request, reader, writer
        self.account = request["account"]
        self.call = request["call"]
        self.device = None
        self.members = []
        self.keys = []
        self.task = None
        self.failure = None
        self.index = 0
        self.sender_key = bytearray()
        self._lock = asyncio.Lock()
        self.remote_ready = asyncio.Event()

    async def rpc(self, *, key=None):
        async with self._lock:
            request_id = str(uuid.uuid4())
            event = {"event": "matrix_crypto", "request_id": request_id, "op": "send" if key else "poll"}
            if key:
                event.update(key=key, index=self.index)
            self.writer.write((json.dumps(event) + "\n").encode())
            await self.writer.drain()
            line = await asyncio.wait_for(self.reader.readline(), timeout=45)
            if not line or len(line) > 65536:
                raise RuntimeError("Matrix crypto control disconnected")
            response = json.loads(line)
            if response.get("type") != "matrix_crypto_result" or response.get("request_id") != request_id or response.get("error"):
                raise RuntimeError("Matrix crypto control failed")
            return response["result"]

    async def prepare(self):
        result = await self.rpc()
        self.device = result["device_id"]
        self.members, self.keys = result["members"], result["keys"]
        expected = self.call.get("caller_device")
        if expected and not any(m["identity"] == self.call["caller"]+":"+expected and
                                (not self.call.get("membership_id") or m["membership_id"] == self.call["membership_id"])
                                for m in self.members):
            raise PermissionError("Matrix caller device membership changed")
        return self.device

    def options(self, rtc):
        # Element uses raw key material with HKDF and a 256-entry sender index
        # space; shared-key/PBKDF2 defaults are incompatible with MatrixRTC.
        return rtc.RoomOptions(connect_timeout=15, e2ee=rtc.E2EEOptions(
            key_provider_options=rtc.KeyProviderOptions(shared_key=None,
                ratchet_salt=b"LKFrameEncryptionKey", ratchet_window_size=10,
                key_ring_size=256, key_derivation_function=1)))

    def allows(self, identity):
        expected = self.call.get("caller_device")
        if expected:
            return identity == self.call["caller"]+":"+expected
        return any(member["identity"] == identity for member in self.members)

    async def authorize(self, room, identity):
        # A track can arrive before the next half-second control update.
        # Refresh the authenticated membership instead of accepting a prefix.
        if not self.allows(identity):
            self.install(room, await self.rpc())
        return self.allows(identity)

    def install(self, room, result):
        if result["device_id"] != self.device:
            raise PermissionError("Matrix media device changed")
        ready = False
        for item in result["keys"]:
            if not any(member["identity"] == item["identity"] and member["membership_id"] == item["membership_id"]
                       for member in result["members"]):
                continue
            key = base64.b64decode(item["key"] + "=" * (-len(item["key"]) % 4), altchars=b"-_", validate=True)
            if len(key) != 16:
                raise ValueError("invalid remote Matrix media key")
            room.e2ee_manager.key_provider.set_key(item["identity"], key, item["index"])
            expected = self.call.get("caller_device")
            if not expected or item["identity"] == self.call["caller"]+":"+expected:
                ready = True
        self.members = result["members"]
        if ready:
            self.remote_ready.set()
        else:
            self.remote_ready.clear()

    async def wait_ready(self):
        await asyncio.wait_for(self.remote_ready.wait(), 30)
        if self.failure:
            raise RuntimeError("Matrix media key lifecycle failed")

    def set_sender(self, room):
        identity = self.account["matrix_user_id"]+":"+self.device
        if room.local_participant.identity != identity:
            raise PermissionError("SFU identity differs from Matrix device")
        self.sender_key[:] = secrets.token_bytes(16)
        room.e2ee_manager.key_provider.set_key(identity, bytes(self.sender_key), self.index)
        for cryptor in room.e2ee_manager.frame_cryptors():
            if cryptor.participant_identity == identity:
                cryptor.set_key_index(self.index)

    async def start(self, room, terminate):
        self.set_sender(room)
        material = base64.b64encode(self.sender_key).decode().rstrip("=")
        self.install(room, await self.rpc(key=material))

        async def updates():
            try:
                while True:
                    await asyncio.sleep(self.POLL_SECONDS)
                    result = await self.rpc()
                    old = {(m["identity"], m["membership_id"]) for m in self.members}
                    new = {(m["identity"], m["membership_id"]) for m in result["members"]}
                    # Sender keys change when a participant/device leaves or
                    # its membership is replaced. Send before switching media.
                    if old - new:
                        self.index = (self.index + 1) % 256
                        next_key = secrets.token_bytes(16)
                        sent = await self.rpc(key=base64.b64encode(next_key).decode().rstrip("="))
                        await asyncio.sleep(self.USE_KEY_DELAY)
                        self.sender_key[:] = next_key
                        identity = room.local_participant.identity
                        room.e2ee_manager.key_provider.set_key(identity, next_key, self.index)
                        for cryptor in room.e2ee_manager.frame_cryptors():
                            if cryptor.participant_identity == identity:
                                cryptor.set_key_index(self.index)
                        result = sent
                    elif new - old:
                        # A new/reconnected device needs a fresh encrypted key
                        # event even when our sender key did not rotate.
                        result = await self.rpc(key=base64.b64encode(self.sender_key).decode().rstrip("="))
                    self.install(room, result)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.failure = type(error).__name__
                terminate()
        self.task = asyncio.create_task(updates())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.sender_key[:] = b"\x00" * len(self.sender_key)
        self.keys.clear()
        self.members.clear()
