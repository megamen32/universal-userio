"""Answer one incoming Matrix call on the voice host with the Secretary SDK.

This is the callee half of a Matrix call. The UserIO runtime detected the
incoming call and handed it over the existing control transport; here the voice
host answers as the called account, joins the call and lets the existing
Secretary brain answer it.

The person identity is never taken from the request: it is resolved from this
host's verified identity registry, exactly like every other Secretary call. An
unmapped caller is refused instead of falling back to the guest branch.

No dialing, no second caller, no incident context: the first mode is secretary
and the mode follows the existing SDK handoff contract.
"""
from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping

from .agentcall_realtime_bridge import (
    BackendConfig,
    EventBroker,
    LocalSpeechPipeline,
    PreparedCall,
    handle_call,
    load_wait_prompts,
    pack_audiosocket_frame,
)

MEMBER = "org.matrix.msc3401.call.member"
CALLEE_ACTION = "matrix_answer_transport"


def _request(server: str, token: str, method: str, path: str, payload=None) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def resolve_person(caller: str, identities: Mapping[str, str]) -> str:
    """Resolve the caller's person UUID in this host's verified registry."""
    person = identities.get("matrix:" + caller, "")
    if not person:
        raise PermissionError("matrix caller is not a registered person on this host")
    uuid.UUID(person)
    return person


def load_identities(settings: Mapping[str, str]) -> Mapping[str, str]:
    from agentcall_secretary_voice import private_file
    import json as _json
    return _json.loads(private_file("EXMANAGER_VOICE_IDENTITIES_FILE", settings))


class _ControlBroker:
    """Forward engine events to the control stream and keep the answer text."""

    def __init__(self, inner: EventBroker, writer: asyncio.StreamWriter) -> None:
        self.inner = inner
        self.writer = writer
        self.assistant_text = ""
        self.turns = 0

    async def accept(self, reader, writer) -> None:
        await self.inner.accept(reader, writer)

    async def emit(self, event: Mapping[str, Any]) -> None:
        if event.get("type") == "transcript_final" and event.get("speaker") == "assistant":
            self.assistant_text += " " + str(event.get("text") or "")
            self.turns += 1
        await self.inner.emit(dict(event))


class _RtcWriter:
    """The engine's audio sink: capture the local track for the SFU.

    The engine writes audio synchronously and awaits the drain, exactly like the
    AudioSocket writer it replaces; the drain is where the frames reach the
    LiveKit source.
    """

    def __init__(self, source, hung_up: asyncio.Event, rtc: Any = None) -> None:
        self.source = source
        self.hung_up = hung_up
        self.rtc = rtc
        self.sample_rate = 48000
        self.paces_audio = True
        self.pending = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.pending.extend(data)

    async def drain(self) -> None:
        rtc = self.rtc
        if rtc is None:
            from livekit import rtc
        while len(self.pending) >= 3:
            length = int.from_bytes(self.pending[1:3], "big")
            if len(self.pending) < 3 + length:
                return
            kind = self.pending[0]
            pcm = bytes(self.pending[3:3 + length])
            del self.pending[:3 + length]
            if kind == 0x16:
                await self.source.capture_frame(rtc.AudioFrame(pcm, 48000, 1, len(pcm) // 2))
            elif kind == 0x02:
                self.hung_up.set()

    def can_write_eof(self) -> bool:
        return True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, name: str, default=None):
        return default

    async def emit(self, event: Mapping[str, Any]) -> None:
        return None


async def answer_incoming_call(request: Mapping[str, Any], config: BackendConfig, *,
                               broker: EventBroker, writer: asyncio.StreamWriter,
                               identities: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Answer the admitted call, run the Secretary and return the terminal receipt."""
    from livekit import rtc

    call = dict(request.get("call") or {})
    account = dict(request.get("account") or {})
    identity = dict(request.get("identity") or {})
    rtc_auth_url = str(request.get("rtc_auth_url") or "").rstrip("/")
    room_id = str(call.get("room_id") or "")
    caller = str(call.get("caller") or "")
    if not room_id or not caller or not account.get("access_token") or not rtc_auth_url:
        raise ValueError("incomplete Matrix answer request")
    settings = identities if identities is not None else load_identities(dict(config.__dict__) if hasattr(config, "__dict__") else {})
    person = resolve_person(caller, settings)

    server = str(account["homeserver"])
    token = str(account["access_token"])
    own_user_id = str(account.get("matrix_user_id") or "")
    device = str(identity.get("device_id") or "")
    if not device:
        devices = _request(server, token, "GET", "/_matrix/client/v3/devices")
        device = str((devices.get("devices") or [{}])[0].get("device_id") or "")
        if not device:
            raise ValueError("Matrix answer device is unknown")

    room_path = "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id, safe="")
    state = _request(server, token, "GET", room_path + "/state")
    joined = {str(event.get("state_key")) for event in state
              if event.get("type") == "m.room.member"
              and (event.get("content") or {}).get("membership") == "join"}
    if own_user_id not in joined or caller not in joined:
        raise PermissionError("Matrix call room does not hold caller and callee")
    focus = dict(call.get("focus") or {})
    if not focus.get("livekit_service_url"):
        raise ValueError("Matrix call has no LiveKit focus")

    state_key = "_" + own_user_id + "_" + device + "_m.call"
    content = {
        # The published membership keeps the room-scoped call id of the working
        # caller; the internal engine uuid never reaches the wire.
        "application": "m.call", "call_id": "", "device_id": device,
        "expires": int(call.get("expires_ms") or 240000),
        "foci_preferred": [focus], "focus_active": {"focus_selection": "oldest_membership", "type": "livekit"},
        "m.call.intent": "audio", "membershipID": str(uuid.uuid4()), "scope": "m.room",
    }
    membership_path = room_path + "/state/" + MEMBER + "/" + urllib.parse.quote(state_key, safe="")
    openid = _request(server, token, "POST",
                      "/_matrix/client/v3/user/" + urllib.parse.quote(own_user_id, safe="") + "/openid/request_token", {})
    sfu = _request(rtc_auth_url, "", "POST", "/sfu/get",
                    {"room": room_id, "openid_token": openid, "device_id": device})
    room = rtc.Room()
    source = rtc.AudioSource(48000, 1, queue_size_ms=200)
    audio_reader = asyncio.StreamReader(limit=1024 * 1024)
    stopped = asyncio.Event()
    media_tasks: set[asyncio.Task] = set()
    published = False

    async def capture(track) -> None:
        try:
            stream = rtc.AudioStream(track, sample_rate=48000, num_channels=1, frame_size_ms=20, capacity=100)
            async for event in stream:
                if stopped.is_set():
                    break
                frame = bytes(event.frame.data)
                if len(audio_reader._buffer) + len(frame) + 3 > 1024 * 1024:
                    raise BufferError("Matrix incoming audio exceeds bounded buffer")
                audio_reader.feed_data(pack_audiosocket_frame(0x16, frame))
        except Exception:
            pass

    @room.on("track_subscribed")
    def subscribed(track, publication, participant) -> None:
        if participant.identity.startswith(caller + ":"):
            task = asyncio.create_task(capture(track))
            media_tasks.add(task)
            task.add_done_callback(media_tasks.discard)

    await room.connect(sfu["url"], sfu["jwt"], rtc.RoomOptions(connect_timeout=15))
    # The membership is published only after the media is ready, so a failed
    # setup never leaves a stale call member behind.
    _request(server, token, "PUT", membership_path, content)
    published = True
    await room.local_participant.publish_track(
        rtc.LocalAudioTrack.create_audio_track("UserIO Secretary", source),
        rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
    )

    pipeline = LocalSpeechPipeline(config)
    greeting_pcm, attention_pcm = await asyncio.gather(
        asyncio.to_thread(pipeline.synthesize, "Здравствуйте. Я голосовой секретарь. Слушаю вас."),
        asyncio.to_thread(pipeline.synthesize, config.attention_prompt),
    )
    prepared = PreparedCall(
        "Здравствуйте. Я голосовой секретарь. Слушаю вас.", greeting_pcm, "", b"\x00\x00" * 160,
        "Владелец звонит личному помощнику через Matrix. Выясни его задачу, не предполагай цель звонка.",
        {"call_mode": "incoming", "voice_transport": "matrix", "voice_target": caller},
        time.time(), load_wait_prompts(),
        account_ref=str(account.get("id") or ""), peer_ref=room_id, peer_label=caller,
        host_answered_incoming=True,
    )
    control_broker = _ControlBroker(broker, writer)
    # The engine expects the call id as the first audio frame.
    engine_call_id = str(call.get("engine_call_id") or uuid.uuid4())
    audio_reader.feed_data(pack_audiosocket_frame(UUID_KIND, uuid.UUID(engine_call_id).bytes))
    max_seconds = int(call.get("max_seconds") or 120)
    if not 30 <= max_seconds <= 600:
        max_seconds = 120
    try:
        await asyncio.wait_for(
            handle_call(
                audio_reader, _RtcWriter(source, stopped), control_broker, config,
                {engine_call_id: prepared},
                answer_gate=lambda _call_id, hung_up: not hung_up.is_set(),
            ),
            timeout=max_seconds + 120)
    finally:
        stopped.set()
        for task in list(media_tasks):
            task.cancel()
        if published:
            # The call member of this participant never outlives the call.
            try:
                _request(server, token, "PUT", membership_path, {})
            except Exception:
                pass
        await room.disconnect()
        await source.aclose()
    return {
        "type": "receipt",
        "state": "completed" if control_broker.assistant_text else "unanswered",
        "text": control_broker.assistant_text.strip(),
        "person": person,
        "callId": engine_call_id,
        "turns": control_broker.turns,
    }
