#!/usr/bin/env python3
"""Fixture-only MatrixRTC receiver that runs UserIO's existing voice engine.

Run inside the deployed ``userio-voice`` container with fixture alias ``a`` or
``b``. It waits for the other fixture to originate in the dedicated room,
answers once using this fixture's saved model, and hands remote PCM to
``agentcall_realtime_bridge.handle_call``. It never originates a call or sends
a room message.
"""
from __future__ import annotations

import asyncio
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid


DB = "/var/lib/universal-userio/userio.sqlite3"
ROOM = "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com"
OWNER_MATRIX_ID = "@bezrabotnyi:chat.bezrabotnyi.com"
FIXTURES = {
    "a": {"username": "lhc_voice_user_a_20261021",
          "matrix_id": "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"},
    "b": {"username": "lhc_voice_user_b_20261021",
          "matrix_id": "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"},
}
MAX_SECONDS = 240
MAX_INCOMING_AUDIO_BUFFER_BYTES = 1024 * 1024


def _request(server: str, token: str, method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Matrix response exceeds limit")
    return json.loads(raw)


def fixture_credentials(alias):
    """Load only one exact fixture account and its stored model alias."""
    from universal_userio.account_connections import ConnectorSecrets

    fixture = FIXTURES[alias]
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        user = db.execute("select id,role from users where username=?",
                          (fixture["username"],)).fetchone()
        if not user or user["role"] != "user":
            raise PermissionError("fixture_user_not_an_ordinary_user")
        account = db.execute(
            "select id,credential_ref from provider_accounts where user_id=? "
            "and provider='matrix' and enabled=1", (user["id"],),
        ).fetchone()
        ai = db.execute(
            "select endpoint,model,token from user_ai_settings where user_id=?", (user["id"],),
        ).fetchone()
    finally:
        db.close()
    if not account or not ai or ai["model"] != "omniroute/minimax-combo-m3.1":
        raise PermissionError("fixture_matrix_or_model_settings_missing")
    secret = ConnectorSecrets().load(
        str(account["credential_ref"]), user_id=str(user["id"]), provider="matrix",
    )
    if secret.get("matrix_user_id") != fixture["matrix_id"]:
        raise PermissionError("fixture_matrix_identity_mismatch")
    return str(user["id"]), str(account["id"]), secret, {
        "endpoint": str(ai["endpoint"]), "model": str(ai["model"]), "token": str(ai["token"]),
    }


def _fixture_identity_map(document, caller_id):
    """Copy the owner mapping to only the active test caller, fail closed."""
    allowed = {item["matrix_id"] for item in FIXTURES.values()}
    if caller_id not in allowed or not isinstance(document, dict):
        raise PermissionError("fixture_identity_scope_invalid")
    owner_ids = {value for key, value in document.items()
                 if isinstance(key, str) and key.startswith("phone:") and isinstance(value, str)}
    if len(owner_ids) != 1:
        raise PermissionError("owner_phone_identity_not_unique")
    owner_id = next(iter(owner_ids))
    uuid.UUID(owner_id)
    mapping_key = "matrix:" + caller_id
    existing = document.get(mapping_key)
    if existing and existing != owner_id:
        raise PermissionError("fixture_identity_already_bound_differently")
    return {**document, mapping_key: owner_id}


class FixtureSecretaryScope:
    """Use a private temporary config map; never edit the live host identity file."""

    def __init__(self, caller_id):
        self.caller_id = caller_id
        self.temp = None
        self.previous = {}

    @staticmethod
    def _write_private(path, payload):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())

    def __enter__(self):
        from agentcall_secretary_voice import CONFIG_PATH, private_file

        config_source = os.environ.get("EXMANAGER_VOICE_CONFIG_FILE") or CONFIG_PATH
        config = json.loads(private_file("config", {"config": config_source}))
        identity_text = private_file("EXMANAGER_VOICE_IDENTITIES_FILE", config)
        identities = _fixture_identity_map(json.loads(identity_text), self.caller_id)
        self.temp = tempfile.TemporaryDirectory(prefix="lhc-matrix-secretary-")
        os.chmod(self.temp.name, 0o700)
        identity_path = Path(self.temp.name) / "identities.json"
        config_path = Path(self.temp.name) / "secretary.json"
        self._write_private(identity_path, identities)
        config["EXMANAGER_VOICE_IDENTITIES_FILE"] = str(identity_path)
        self._write_private(config_path, config)
        for name in ("EXMANAGER_VOICE_URL", "EXMANAGER_VOICE_CONFIG_FILE"):
            self.previous[name] = os.environ.get(name)
        os.environ.pop("EXMANAGER_VOICE_URL", None)
        os.environ["EXMANAGER_VOICE_CONFIG_FILE"] = str(config_path)
        return self

    def __exit__(self, exc_type, exc, traceback):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if self.temp is not None:
            self.temp.cleanup()


def _modes_seen(log_text: str) -> list[str]:
    return sorted(set(re.findall(r"voice_mode .*?mode=(secretary|incidents)", log_text)))


async def run():
    from livekit import rtc
    from universal_userio.matrix_calls import MEMBER
    from universal_userio.matrix_call_worker import (
        finish_rtc_audio, server_voice_config, voice_config_for_request,
    )

    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", choices=tuple(FIXTURES))
    parser.add_argument("--room-id", default=ROOM)
    parser.add_argument("--caller-id", default="")
    parser.add_argument("--reply-turns", type=int, choices=(1, 2), default=2)
    args = parser.parse_args()
    alias = args.fixture
    fixture = FIXTURES[alias]
    room_id = str(args.room_id)
    if not re.fullmatch(r"![A-Za-z0-9]+:chat\.bezrabotnyi\.com", room_id):
        raise ValueError("fixture_room_id_invalid")
    default_caller = FIXTURES["b" if alias == "a" else "a"]["matrix_id"]
    caller_id = str(args.caller_id or default_caller)
    allowed_callers = {item["matrix_id"] for item in FIXTURES.values()} | {OWNER_MATRIX_ID}
    if caller_id not in allowed_callers or caller_id == fixture["matrix_id"]:
        raise PermissionError("fixture_caller_identity_not_allowed")
    peer = {"matrix_id": caller_id}
    user_id, account_id, account, voice_ai = fixture_credentials(alias)
    homeserver, access = account["homeserver"], account["access_token"]
    identity = _request(homeserver, access, "GET", "/_matrix/client/v3/account/whoami")
    if identity.get("user_id") != fixture["matrix_id"]:
        raise PermissionError("fixture_matrix_whoami_mismatch")

    room_path = "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id, safe="")
    state = _request(homeserver, access, "GET", room_path + "/state")
    members = {event.get("state_key"): event.get("content", {}).get("membership")
               for event in state if event.get("type") == "m.room.member"}
    joined = {member for member, membership in members.items() if membership == "join"}
    if joined != {fixture["matrix_id"], caller_id}:
        raise PermissionError("fixture_room_membership_changed")
    if any(event.get("type") == "m.room.encryption" for event in state):
        raise ValueError("encrypted_room_not_supported")

    devices = _request(homeserver, access, "GET", "/_matrix/client/v3/devices").get("devices", [])
    device = next((str(item.get("device_id") or "") for item in devices
                   if item.get("device_id")), "")
    if not device:
        raise PermissionError("fixture_matrix_device_missing")

    # Prepare the existing call engine before accepting an incoming ring.
    sys.path.insert(0, "/usr/local/lib/agentcall")
    from agentcall_realtime_bridge import (
        BackendConfig, LocalSpeechPipeline, PreparedCall,
        handle_call, load_wait_prompts, pack_audiosocket_frame, UUID_KIND,
    )
    import agentcall_realtime_bridge as voice

    config = voice_config_for_request(
        server_voice_config(BackendConfig.from_environment()), voice_ai,
    )
    os.environ["AGENTCALL_TTS_PHONE_FALLBACK"] = "0"
    greeting = "Здравствуйте. Проверим, что Matrix-звук работает в обе стороны."
    pipeline = LocalSpeechPipeline(config)
    greeting_pcm, attention_pcm = await asyncio.gather(
        asyncio.to_thread(pipeline.synthesize, greeting),
        asyncio.to_thread(pipeline.synthesize, config.attention_prompt),
    )
    prepared_call_id = str(uuid.uuid4())
    call_context = (
        "Закрытый тест двух выданных владельцем Matrix-аккаунтов. Это тестовый "
        "собеседник, не владелец и не Secretary. Отвечай естественно и кратко. "
        "Не запрашивай личные сведения, не отправляй сообщения, не создавай "
        "инциденты и не управляй другими звонками или сессиями."
    )
    prepared = PreparedCall(
        greeting_text=greeting, greeting_pcm=greeting_pcm,
        attention_text=config.attention_prompt, attention_pcm=attention_pcm,
        context=call_context,
        agent_context={
            "call_mode": "incoming_guest", "voice_transport": "matrix",
            "voice_target": peer["matrix_id"],
        },
        created_at=time.time(), wait_prompts=load_wait_prompts(),
        account_ref=account_id, peer_ref=room_id, peer_label="Matrix caller " + caller_id,
    )

    rtc_url = os.environ.get("USERIO_MATRIX_RTC_AUTH_URL", "").rstrip("/")
    if not rtc_url:
        raise RuntimeError("MatrixRTC auth endpoint is not configured")
    room = rtc.Room()
    source = rtc.AudioSource(48000, 1, queue_size_ms=200)
    audio_reader = asyncio.StreamReader(limit=1024 * 1024)
    stop = asyncio.Event()
    transport_ready = asyncio.Event()
    media_tasks: set[asyncio.Task] = set()
    summaries = {"assistant_turns": 0, "caller_turns": 0, "errors": 0, "modes": []}
    room_path = "/_matrix/client/v3/rooms/" + urllib.parse.quote(room_id, safe="")

    async def api(method, path, payload=None):
        return await asyncio.to_thread(_request, homeserver, access, method, path, payload)

    class Writer:
        sample_rate = 48000
        paces_audio = True

        def __init__(self):
            self.pending = bytearray()
            self.closed = False

        def write(self, data):
            self.pending.extend(data)

        async def drain(self):
            import struct
            while len(self.pending) >= 3:
                length = struct.unpack("!H", self.pending[1:3])[0]
                if len(self.pending) < 3 + length:
                    return
                kind = self.pending[0]
                pcm = bytes(self.pending[3:3 + length])
                del self.pending[:3 + length]
                if kind == 0x16:
                    await source.capture_frame(rtc.AudioFrame(pcm, 48000, 1, len(pcm) // 2))
                elif kind == voice.HANGUP_KIND:
                    stop.set()

        async def finish_audio(self, *, should_stop=None, interrupted=False):
            await finish_rtc_audio(source, should_stop, interrupted=interrupted)

        def close(self):
            self.closed = True
            stop.set()

        def is_closing(self): return self.closed
        async def wait_closed(self): return None
        def get_extra_info(self, name, default=None): return default

    class Broker:
        async def emit(self, event):
            kind = event.get("event")
            if kind == "transcript_final":
                if event.get("speaker") == "remote":
                    summaries["caller_turns"] += 1
                else:
                    summaries["assistant_turns"] += 1
                # The first assistant transcript is the prepared greeting.
                # End only after the owner has spoken one reply to B's one
                # generated answer; never let B answer the owner's reply.
                if (summaries["caller_turns"] >= args.reply_turns + 1
                        and summaries["assistant_turns"] >= args.reply_turns + 1
                        and not stop.is_set()):
                    stop.set()
                    audio_reader.feed_data(pack_audiosocket_frame(voice.HANGUP_KIND, b""))
                    await asyncio.sleep(0)
            elif kind == "error":
                summaries["errors"] += 1
            elif kind == "voice_mode":
                summaries["modes"].append(str(event.get("mode") or ""))

    writer = Writer()
    voice_call_id = str(uuid.uuid4())
    engine = None
    joined_rtc = False
    joined_matrix = False
    started_ms = int(time.time() * 1000)
    initial_state = await api("GET", room_path + "/state")
    previous_events = {event.get("event_id") for event in initial_state
                       if event.get("type") == MEMBER}

    async def receive(track):
        stream = rtc.AudioStream(track, sample_rate=48000, num_channels=1,
                                 frame_size_ms=20, capacity=100)
        try:
            await transport_ready.wait()
            async for event in stream:
                if stop.is_set():
                    break
                frame = bytes(event.frame.data)
                if len(audio_reader._buffer) + len(frame) + 3 > MAX_INCOMING_AUDIO_BUFFER_BYTES:
                    raise BufferError("fixture incoming audio exceeds bounded buffer")
                audio_reader.feed_data(pack_audiosocket_frame(0x16, frame))
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def subscribed(track, publication, participant):
        if (track.kind == rtc.TrackKind.KIND_AUDIO
                and participant.identity.startswith(peer["matrix_id"] + ":")):
            task = asyncio.create_task(receive(track))
            media_tasks.add(task)
            task.add_done_callback(media_tasks.discard)

    @room.on("participant_disconnected")
    def disconnected(participant):
        if participant.identity.startswith(peer["matrix_id"] + ":"):
            stop.set()
            audio_reader.feed_data(pack_audiosocket_frame(voice.HANGUP_KIND, b""))

    try:
        deadline = time.monotonic() + 180
        caller_event = None
        print(json.dumps({"event": "ready", "fixture": alias,
                          "caller": caller_id, "room": room_id,
                          "reply_turns": args.reply_turns, "model": voice_ai["model"]}), flush=True)
        while time.monotonic() < deadline:
            current = await api("GET", room_path + "/state")
            candidates = [event for event in current
                if event.get("type") == MEMBER and event.get("sender") == peer["matrix_id"]
                and str(event.get("state_key") or "").startswith("_" + peer["matrix_id"] + "_")
                and event.get("event_id") not in previous_events
                and event.get("origin_server_ts", 0) >= started_ms
                and any(focus.get("type") == "livekit"
                    and focus.get("livekit_service_url", "").rstrip("/") == rtc_url
                    for focus in event.get("content", {}).get("foci_preferred", [])
                    if isinstance(focus, dict))]
            if candidates:
                caller_event = max(candidates, key=lambda item: item.get("origin_server_ts", 0))
                break
            await asyncio.sleep(1)
        if caller_event is None:
            print(json.dumps({"event": "timeout_waiting_for_fixture_call"}), flush=True)
            return 2

        openid = await api(
            "POST", "/_matrix/client/v3/user/" + urllib.parse.quote(fixture["matrix_id"], safe="")
            + "/openid/request_token", {},
        )
        sfu = await asyncio.to_thread(
            _request, rtc_url, "", "POST", "/sfu/get",
            {"room": room_id, "openid_token": openid, "device_id": device},
        )
        await room.connect(sfu["url"], sfu["jwt"], rtc.RoomOptions(connect_timeout=15))
        joined_rtc = True
        await room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("UserIO Secretary fixture " + alias, source),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        transport_ready.set()
        audio_reader.feed_data(pack_audiosocket_frame(UUID_KIND, uuid.UUID(voice_call_id).bytes))
        content = caller_event["content"]
        membership_id = str(content.get("call_id") or "")
        state_key = "_" + fixture["matrix_id"] + "_" + device + "_m.call"
        state_path = room_path + "/state/" + MEMBER + "/" + urllib.parse.quote(state_key, safe="")
        await api("PUT", state_path, {
            "application": "m.call", "call_id": membership_id, "device_id": device,
            "expires": MAX_SECONDS * 1000, "foci_preferred": content["foci_preferred"],
            "focus_active": content.get("focus_active"), "m.call.intent": "audio",
            "membershipID": str(uuid.uuid4()), "scope": "m.room",
        })
        joined_matrix = True
        print(json.dumps({"event": "answered", "voice_call_id": voice_call_id}), flush=True)
        log_dir = Path("/tmp/lhc-matrixrtc-live-peer")
        log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        log_path = log_dir / (voice_call_id + ".engine.log")
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        log_handle = os.fdopen(fd, "w", encoding="utf-8")
        previous_stderr = sys.stderr
        try:
            sys.stderr = log_handle

            async def fixture_answer_gate(_call_id, hung_up):
                return not hung_up.is_set()

            engine = asyncio.create_task(handle_call(
                audio_reader, writer, Broker(), config, {voice_call_id: prepared},
                answer_gate=fixture_answer_gate,
            ))
            try:
                await asyncio.wait_for(engine, timeout=MAX_SECONDS)
            except asyncio.TimeoutError:
                stop.set()
                audio_reader.feed_data(pack_audiosocket_frame(voice.HANGUP_KIND, b""))
                audio_reader.feed_eof()
                await asyncio.wait_for(engine, timeout=config.request_timeout + 15)
            await source.wait_for_playout()
        finally:
            log_handle.flush()
            sys.stderr = previous_stderr
            log_handle.close()
        log_text = log_path.read_text(errors="replace")
        modes = _modes_seen(log_text)
        print(json.dumps({"event": "finished", "fixture": alias,
                          "voice_call_id": voice_call_id,
                          "engine_log": str(log_path), "modes_seen": modes,
                          **summaries}), flush=True)
        return 0 if (summaries["caller_turns"] >= args.reply_turns + 1
                     and summaries["assistant_turns"] >= args.reply_turns + 1
                     and summaries["errors"] == 0) else 3
    finally:
        stop.set()
        if engine is not None and not engine.done():
            audio_reader.feed_eof()
            engine.cancel()
            await asyncio.gather(engine, return_exceptions=True)
        for task in list(media_tasks):
            task.cancel()
        if media_tasks:
            await asyncio.gather(*media_tasks, return_exceptions=True)
        if joined_matrix:
            try:
                await api("PUT", state_path, {})
            except Exception:
                pass
        if joined_rtc:
            await room.disconnect()
        await source.aclose()


def main():
    try:
        return asyncio.run(run())
    except Exception as error:
        # Do not print exception text: upstream errors may carry provider data.
        print(json.dumps({"event": "error", "reason": type(error).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
