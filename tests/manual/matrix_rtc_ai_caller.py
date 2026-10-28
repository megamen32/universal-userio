#!/usr/bin/env python3
"""Fixture-only MatrixRTC CALLER with a fixed, non-LLM first phrase (canary mavis09).

Why this exists (canary mavis09, leaf L04, 24.10.2026):

  The product caller path (matrix_call_worker -> CallController.prepare ->
  generate_opening -> LocalSpeechPipeline.opening) always re-phrases the
  caller's first phrase with an LLM before it is spoken
  (agentcall_realtime_bridge.py:2528, :2198, :1826-1848).  The canary
  therefore cannot measure a fixed question: the question the SECRETARY hears
  is an LLM paraphrase (measured 08:44: "наблюдатель на домене безработный.com
  отдаёт 5" - the domain renamed in Russian and the code 502 collapsed to 5).

  This caller keeps the whole real transport and the REAL voice engine
  (the canary bridge, md5 8b9daf943f7096b62d4853030bf2110f) and only replaces
  the two LLM-generated phrases:
    * the first phrase is a prepared, fixed TTS phrase (no LLM at all);
    * the second phrase is answered by a local, fixed-phrase LLM stub, so the
      second caller turn is a fixed, contentful question (no LLM at all).
  Everything else - Matrix signalling, the SFU, the recognizer, the turn
  detector, the recording and the SECRETARY on the other side - is product
  code.  The SECRETARY answers through its own brain (ExManager), untouched.

  It never originates a call for a real account, never touches a live
  container and never uses the owner's phone: it only uses the two test
  Matrix accounts of the fixture pair in the dedicated test room.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path

DB = "/var/lib/universal-userio/userio.sqlite3"
ROOM = os.environ.get("CALLER_ROOM", "!ZatzpywRZSemgAaPju:chat.bezrabotnyi.com")
RTC_URL = os.environ.get("USERIO_MATRIX_RTC_AUTH_URL", "https://rtc.chat.bezrabotnyi.com").rstrip("/")
MEMBER = "org.matrix.msc3401.call.member"
RING = "org.matrix.msc4075.rtc.notification"
CALLER = "lhc_voice_user_a_20261021"
TARGET = "@lhc_voice_tester_b_20261021:chat.bezrabotnyi.com"
MAX_SECONDS = 240
# The fixed first phrase: the domain and the code are spoken the way the
# product speaks them (text_for_speech: the site is named in Russian, a code
# after the word "код" is read digit by digit).  Measured STT round trip in the
# canary: "Наблюдатель безработной точка ком отдаёт Cat 502." - the domain and
# the code 502 both survive recognition (report section 4.2).
FIRST_PHRASE = ("Наблюдатель безработный точка ком отдаёт код пятьсот два. "
                "Что это значит и что следует проверить?")
FIRST_WAV = os.environ.get("CALLER_FIRST_WAV", "/tmp/caller_first.wav")
# Scripted phrases of the caller: one per substantive answer of the callee.
SCRIPT_WAVS = [item for item in os.environ.get("CALLER_SCRIPT_WAVS", "").split(",") if item]
# The scripted phrase follows the answer it answers, so the caller never
# interrupts the callee while it is still speaking.
SCRIPT_QUIET_SECONDS = float(os.environ.get("CALLER_SCRIPT_QUIET_SECONDS", "4") or "4")
# The form of a real client: the call membership of the caller and no ring.
CALLER_CLIENT_FORM = os.environ.get("CALLER_CLIENT_FORM", "") not in ("", "0", "false")
# The canary caller is a listener: it must not end the call because its own
# recognition of the callee was late. The Secretary may need a minute to think.
CALLER_IDLE_TIMEOUT_SECONDS = float(os.environ.get("CALLER_IDLE_TIMEOUT_SECONDS", "150") or "150")
# How long the caller is allowed to speak: the question and nothing else.
CALLER_QUESTION_SECONDS = float(os.environ.get("CALLER_QUESTION_SECONDS", "0") or "0")
# The fixed second question, served by the local LLM stub of the canary.
SECOND_PHRASE = ("Проверь, что означает код пять ноль два в ответе наблюдателя "
                 "безработный точка ком, и назови первую проверку.")


def _request(server: str, token: str, method: str, path: str, payload=None):
    body = None if payload is None else json.dumps(payload).encode()
    headers = {"Authorization": f"Bearer {token}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(server + path, data=body, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Matrix response exceeds limit")
    return json.loads(raw)


def _caller_credentials():
    """Load only the fixture caller account and its stored model alias."""
    if os.environ.get("CALLER_TRANSPORT_INPUT") == "private-jsonl":
        # The API-host fixture owner supplies the already bound request. The
        # voice host must not assume it shares the API database or vault.
        request = json.loads(sys.stdin.readline(65537))
        secret = dict(request.get("account") or {})
        if (secret.get("account_id") != "matrix:ab023a689e926bf99d924e933ebd"
                or secret.get("matrix_user_id") != "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com"
                or request.get("call", {}).get("room_id") != ROOM
                or request.get("call", {}).get("target") != TARGET
                or not secret.get("user_id")):
            raise PermissionError("fixture_private_transport_binding_mismatch")
        return secret["user_id"], secret["account_id"], secret
    from universal_userio.account_connections import ConnectorSecrets

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        user = db.execute("select id,role from users where username=?", (CALLER,)).fetchone()
        if not user or user["role"] != "user":
            raise PermissionError("fixture_caller_user_missing")
        account = db.execute(
            "select id,credential_ref from provider_accounts where user_id=? and provider='matrix' and enabled=1",
            (user["id"],),
        ).fetchone()
        ai = db.execute("select endpoint,model,token from user_ai_settings where user_id=?",
                        (user["id"],)).fetchone()
    finally:
        db.close()
    if not account or not ai:
        raise PermissionError("fixture_caller_settings_missing")
    secret = ConnectorSecrets().load(str(account["credential_ref"]), user_id=str(user["id"]),
                                     provider="matrix")
    if secret.get("matrix_user_id") != "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com":
        raise PermissionError("fixture_caller_identity_mismatch")
    return str(user["id"]), str(account["id"]), secret


def write_wav(path: str, pcm) -> None:
    """Write the speech of the fixture with the rate of the audio itself.

    The synthesizer returns its own rate; a header with another rate plays the
    speech faster and the recognizer returns nothing.
    """
    with wave.open(path, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(int(pcm.sample_rate))
        target.writeframes(bytes(pcm))


def _load_wav(path: str):
    """Read a mono PCM16 WAV into native engine PCM."""
    with wave.open(path, "rb") as source:
        if source.getnchannels() != 1 or source.getsampwidth() != 2:
            raise ValueError("caller wav must be mono PCM16")
        pcm = source.readframes(source.getnframes())
        rate = source.getframerate()
    return pcm, rate


async def run():
    from livekit import rtc
    sys.path.insert(0, "/usr/local/lib/agentcall")
    sys.path.insert(0, "/opt/universal-userio/src")
    import agentcall_realtime_bridge as voice
    from universal_userio.matrix_call_worker import (finish_rtc_audio, server_voice_config,
                                                 voice_config_for_request)

    import hashlib
    engine_path = str(Path(voice.__file__))
    with open(engine_path, "rb") as handle:
        engine_md5 = hashlib.md5(handle.read()).hexdigest()
    print(json.dumps({"event": "engine_module", "file": engine_path, "md5": engine_md5}), flush=True)

    user_id, account_id, account = _caller_credentials()
    homeserver, access = account["homeserver"], account["access_token"]
    identity = _request(homeserver, access, "GET", "/_matrix/client/v3/account/whoami")
    if identity.get("user_id") != "@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com":
        raise PermissionError("fixture_caller_whoami_mismatch")

    room_path = "/_matrix/client/v3/rooms/" + urllib.parse.quote(ROOM, safe="")
    state = _request(homeserver, access, "GET", room_path + "/state")
    members = {event.get("state_key"): event.get("content", {}).get("membership")
               for event in state if event.get("type") == "m.room.member"}
    joined = {member for member, membership in members.items() if membership == "join"}
    if joined != {"@lhc_voice_tester_a_20261021:chat.bezrabotnyi.com", TARGET}:
        raise PermissionError("fixture_room_membership_changed")
    crypto = None
    if any(event.get("type") == "m.room.encryption" for event in state):
        # The API-host fixture owner handles these frames with CryptoControl
        # on the existing private runner stream; never tee raw JSONL into logs.
        if os.environ.get("CALLER_CRYPTO_CONTROL") != "private-jsonl":
            raise PermissionError("encrypted fixture requires private account-bound crypto control")
        from universal_userio.matrix_rtc_crypto import MediaEncryption
        control_reader = asyncio.StreamReader(limit=65536)
        await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(control_reader), sys.stdin)
        class ControlWriter:
            def write(self, data): sys.stdout.write(data.decode())
            async def drain(self): sys.stdout.flush()
        crypto = MediaEncryption({"account": account,
                                  "call": {"room_id": ROOM, "target": TARGET}},
                                 control_reader, ControlWriter())
        identity["device_id"] = await crypto.prepare()
    devices = _request(homeserver, access, "GET", "/_matrix/client/v3/devices").get("devices", [])
    device = str(identity.get("device_id") or "") or next((str(item.get("device_id") or "") for item in devices if item.get("device_id")), "")
    if not device:
        raise PermissionError("fixture_caller_device_missing")

    async def api(method, path, payload=None):
        return await asyncio.to_thread(_request, homeserver, access, method, path, payload)

    config = voice_config_for_request(
        server_voice_config(voice.BackendConfig.from_environment()), None)
    # speech_speed=1.0: the canary question must reach the microphone with its
    # own duration, so the outbound gate of the caller holds exactly the
    # question and nothing else.
    config = voice.replace(config, idle_timeout_seconds=CALLER_IDLE_TIMEOUT_SECONDS,
                            speech_speed=1.0)
    # The first phrase is fixed TTS; the second turn is served by the local
    # fixed-phrase stub, so no LLM ever rewrites the caller's own question.
    first_pcm, first_rate = _load_wav(FIRST_WAV)
    question_seconds = len(first_pcm) / (2 * (first_rate or 48000))
    if CALLER_QUESTION_SECONDS:
        question_seconds = CALLER_QUESTION_SECONDS
    # The host engine plays the attention prompt before the greeting, so a
    # non-empty attention would consume the outbound gate of the caller and
    # cut the question itself. The canary caller asks and then only listens.
    attention_pcm = b''
    attention_text = ''
    if first_rate != 48000:
        first_pcm, _ = voice.resample_pcm16(first_pcm, first_rate, 48000, None)

    voice_call_id = str(uuid.uuid4())
    prepared_call = voice.PreparedCall(
        greeting_text=FIRST_PHRASE,
        greeting_pcm=voice.SpeechPCM(first_pcm, 48000),
        attention_text=attention_text,
        attention_pcm=attention_pcm,
        context=FIRST_PHRASE,
        agent_context={"call_mode": "incoming_guest", "voice_transport": "matrix"},
        created_at=time.time(),
        wait_prompts=voice.load_wait_prompts(),
        account_ref=account_id,
        peer_ref=ROOM,
        peer_label=TARGET,
    )

    room = rtc.Room()
    source = rtc.AudioSource(48000, 1, queue_size_ms=200)
    audio_reader = asyncio.StreamReader(limit=1024 * 1024)
    stop = asyncio.Event()
    transport_ready = asyncio.Event()
    media_tasks: set[asyncio.Task] = set()
    joined_rtc = False
    started = int(time.time() * 1000)

    class Writer:
        sample_rate = 48000
        paces_audio = True

        def __init__(self):
            self.pending = bytearray()
            self.closed = False
            # The canary caller speaks the question once and then only listens.
            # Its own phrases must never barge in on the answer of the callee.
            self.spoken_seconds = 0.0
            self.question_seconds = 0.0

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
                    if self.spoken_seconds >= self.question_seconds:
                        continue
                    await source.capture_frame(rtc.AudioFrame(pcm, 48000, 1, len(pcm) // 2))
                    self.spoken_seconds += len(pcm) / (2 * self.sample_rate)
                elif kind == voice.HANGUP_KIND:
                    stop.set()

        async def finish_audio(self, *, should_stop=None, interrupted=False):
            await finish_rtc_audio(source, should_stop, interrupted=interrupted)

        def close(self):
            self.closed = True
            stop.set()

        def is_closing(self):
            return self.closed

        async def wait_closed(self):
            return None

        def get_extra_info(self, name, default=None):
            return default

    remote_answers = 0
    last_remote_at = 0.0
    received_pcm = bytearray()
    last_voice_at = 0.0
    first_heard = False

    class Broker:
        async def emit(self, event):
            nonlocal remote_answers, last_remote_at
            if event.get("event") == "transcript_final" and event.get("speaker") == "remote":
                last_remote_at = time.monotonic()
                if len(str(event.get("text") or "")) >= 40:
                    remote_answers += 1
            print(json.dumps({"event": "caller_bridge", **event}, ensure_ascii=False), flush=True)

    writer = Writer()
    writer.question_seconds = question_seconds

    async def receive(track, publication=None, participant=None):
        nonlocal last_voice_at, first_heard
        if crypto and (publication.encryption_type != rtc.EncryptionType.GCM or
                       not await crypto.authorize(room, participant.identity)):
            stop.set()
            audio_reader.feed_eof()
            return
        stream = rtc.AudioStream(track, sample_rate=48000, num_channels=1,
                                 frame_size_ms=20, capacity=100)
        try:
            await transport_ready.wait()
            async for event in stream:
                if stop.is_set():
                    break
                frame = bytes(event.frame.data)
                if os.environ.get('CALLER_PASSIVE_LISTENER') == '1':
                    from array import array
                    received_pcm.extend(frame)
                    if max((abs(value) for value in array('h', frame)), default=0) > 500:
                        last_voice_at = time.monotonic()
                        if not first_heard:
                            first_heard = True
                            print(json.dumps({'event': 'received_remote_audio',
                                'participant': participant.identity,
                                'encrypted': bool(crypto)}), flush=True)
                    continue
                if len(audio_reader._buffer) + len(frame) + 3 > 1024 * 1024:
                    raise BufferError("caller incoming audio exceeds bounded buffer")
                audio_reader.feed_data(voice.pack_audiosocket_frame(0x16, frame))
        finally:
            await stream.aclose()

    @room.on("track_subscribed")
    def subscribed(track, publication, participant):
        if track.kind == rtc.TrackKind.KIND_AUDIO and participant.identity.startswith(TARGET + ":"):
            task = asyncio.create_task(receive(track, publication, participant))
            media_tasks.add(task)
            task.add_done_callback(media_tasks.discard)

    try:
        # ---- originate the call exactly like the product caller does ----------
        state_key = "_" + account["matrix_user_id"] + "_" + device + "_m.call"
        state_full = room_path + "/state/" + MEMBER + "/" + urllib.parse.quote(state_key, safe="")
        result = await api("PUT", state_full, {
            "application": "m.call", "call_id": "", "device_id": device,
            "expires": MAX_SECONDS * 1000,
            "foci_preferred": [{"type": "livekit", "livekit_service_url": RTC_URL,
                                 "livekit_alias": ROOM}],
            "focus_active": {"focus_selection": "oldest_membership", "type": "livekit"},
            "m.call.intent": "audio", "membershipID": str(uuid.uuid4()), "scope": "m.room",
        })
        if not CALLER_CLIENT_FORM:
            await api("PUT", room_path + "/send/" + RING + "/" + str(uuid.uuid4()), {
                "m.mentions": {"user_ids": [TARGET]}, "notification_type": "ring",
                "m.relates_to": {"event_id": result["event_id"], "rel_type": "m.reference",
                                 "sender_ts": started, "lifetime": 60000, "m.call.intent": "audio"},
            })
        else:
            # The form of a real client: the call membership and no notification.
            print(json.dumps({"event": "originated_client_form",
                              "membership_event_id": result["event_id"]}), flush=True)
        print(json.dumps({"event": "originated", "voice_call_id": voice_call_id}), flush=True)

        deadline = time.monotonic() + 60
        answered = False
        while time.monotonic() < deadline:
            current = await api("GET", room_path + "/state")
            for event in current:
                if (event.get("type") == MEMBER and event.get("sender") == TARGET
                        and event.get("content", {}).get("application") == "m.call"
                        and event.get("content", {}).get("expires", 0) > 0
                        and any(f.get("type") == "livekit"
                                and f.get("livekit_service_url", "").rstrip("/") == RTC_URL
                                for f in event.get("content", {}).get("foci_preferred", [])
                                if isinstance(f, dict))):
                    answered = True
                    break
            if answered:
                break
            await asyncio.sleep(1)
        if not answered:
            print(json.dumps({"event": "timeout_waiting_for_answer"}), flush=True)
            return 2

        openid = await api(
            "POST", "/_matrix/client/v3/user/" + urllib.parse.quote(account["matrix_user_id"], safe="")
            + "/openid/request_token", {})
        sfu = await asyncio.to_thread(
            _request, RTC_URL, "", "POST", "/sfu/get",
            {"room": ROOM, "openid_token": openid, "device_id": device})
        await room.connect(sfu["url"], sfu["jwt"], crypto.options(rtc) if crypto else rtc.RoomOptions(connect_timeout=15))
        joined_rtc = True
        if crypto:
            await crypto.start(room, lambda: (stop.set(), audio_reader.feed_eof()))
        await room.local_participant.publish_track(
            rtc.LocalAudioTrack.create_audio_track("UserIO caller fixture", source),
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        if crypto:
            await crypto.wait_ready()
        transport_ready.set()
        if os.environ.get('CALLER_AUDIO_GATE') == 'private-jsonl':
            if not crypto:
                raise PermissionError('private audio gate requires encrypted fixture control')
            # The same private stream carries media RPC. Hold its lock while
            # the operator binds the actual B engine UUID in the native SDK.
            async with crypto._lock:
                crypto.writer.write((json.dumps({'event': 'fixture_audio_gate'})+'\n').encode())
                await crypto.writer.drain()
                result = json.loads(await asyncio.wait_for(crypto.reader.readline(), 45))
                if result.get('type') != 'fixture_audio_gate_result' or result.get('allowed') is not True:
                    raise PermissionError('native fixture audio scope was not granted')
            print(json.dumps({'event': 'fixture_audio_released', 'call_id': result['call_id']}), flush=True)
        audio_reader.feed_data(voice.pack_audiosocket_frame(voice.UUID_KIND,
                                                           uuid.UUID(voice_call_id).bytes))

        async def fixture_answer_gate(_call_id, hung_up):
            return not hung_up.is_set()

        async def fixture_hangup(_call_id):
            stop.set()

        async def speak(pcm: bytes) -> None:
            frame_bytes = 960 * 2
            for offset in range(0, len(pcm), frame_bytes):
                chunk = pcm[offset:offset + frame_bytes]
                await source.capture_frame(rtc.AudioFrame(chunk, 48000, 1, len(chunk) // 2))

        async def scripted_phrases():
            # The scripted phrase of the caller follows the answer it answers.
            for index, path in enumerate(SCRIPT_WAVS):
                while (remote_answers <= index or not last_remote_at
                       or time.monotonic() - last_remote_at < SCRIPT_QUIET_SECONDS) \
                        and not stop.is_set():
                    await asyncio.sleep(0.25)
                if stop.is_set():
                    return
                pcm, rate = _load_wav(path)
                if rate != 48000:
                    pcm, _ = voice.resample_pcm16(pcm, rate, 48000, None)
                await speak(pcm)
                await asyncio.sleep(1.0)

        if os.environ.get('CALLER_PASSIVE_LISTENER') == '1':
            async def wait_reply(after, timeout=150):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline and not stop.is_set():
                    if last_voice_at > after and time.monotonic()-last_voice_at > 1.6:
                        return
                    await asyncio.sleep(.05)
                raise TimeoutError('fixture remote answer deadline')
            # Let the permanent handler's cached greeting finish, then speak
            # fixed WAVs. This fixture never generates an assistant answer.
            await wait_reply(time.monotonic()-30, 12)
            utterance = 0
            async def completed_answer(stage):
                if os.environ.get('CALLER_WAIT_FULL_ANSWER') != 'private-jsonl':
                    return
                async with crypto._lock:
                    crypto.writer.write((json.dumps({'event': 'fixture_answer_gate',
                        'turn_id': utterance, 'stage': stage, 'phrase_index': index,
                        'utterance_started_at': started_utc, 'utterance_completed_at': time.time()})+'\n').encode())
                    await crypto.writer.drain()
                    result = json.loads(await asyncio.wait_for(crypto.reader.readline(), 180))
                    if result.get('type') != 'fixture_answer_gate_result' or result.get('completed') is not True:
                        raise RuntimeError('Native answer is not durably completed')
            async def wait_substantive_answer():
                if os.environ.get('CALLER_WAIT_FULL_ANSWER') != 'private-jsonl':
                    raise PermissionError('substantive received-audio gate required for barge fixture')
                import hashlib
                prefix = os.environ['CALLER_RECEIVED_WAV'] + '.prefix.wav'
                deadline = time.monotonic()+150
                while time.monotonic() < deadline and not stop.is_set():
                    pcm = bytes(received_pcm[response_audio_start:])[-48000*2*30:]
                    if pcm and last_voice_at > started_phrase:
                        # The operator ASRs this exact immutable snapshot. Only
                        # received B speech matching an SDK answer releases A2.
                        with wave.open(prefix, 'wb') as output:
                            output.setnchannels(1); output.setsampwidth(2); output.setframerate(48000)
                            output.writeframes(pcm)
                        os.chmod(prefix, 0o600)
                        digest = hashlib.sha256(Path(prefix).read_bytes()).hexdigest()
                        async with crypto._lock:
                            crypto.writer.write((json.dumps({'event':'fixture_barge_gate',
                                'audio_sha256':digest, 'utterance_started_at':started_utc})+'\n').encode())
                            await crypto.writer.drain()
                            result = json.loads(await asyncio.wait_for(crypto.reader.readline(), 45))
                        if result.get('type') != 'fixture_barge_gate_result':
                            raise PermissionError('invalid received-audio gate response')
                        if result.get('allowed') is True:
                            return
                    await asyncio.sleep(.5)
                raise TimeoutError('substantive received SDK speech required before interruption')
            for index, path in enumerate([FIRST_WAV, *SCRIPT_WAVS]):
                pcm, rate = _load_wav(path)
                if rate != 48000:
                    pcm, _ = voice.resample_pcm16(pcm, rate, 48000, None)
                started_phrase = time.monotonic()
                started_utc = time.time()
                await speak(pcm)
                await source.wait_for_playout()
                print(json.dumps({'event': 'fixture_phrase_sent', 'index': index}), flush=True)
                utterance += 1
                response_audio_start = len(received_pcm)
                barge = os.environ.get('CALLER_BARGE_WAV') if index == 0 else None
                if barge:
                    await wait_substantive_answer()
                    pcm, rate = _load_wav(barge)
                    if rate != 48000:
                        pcm, _ = voice.resample_pcm16(pcm, rate, 48000, None)
                    voice_age = time.monotonic()-last_voice_at
                    trigger = {'received_pcm_offset':len(received_pcm),
                               'triggered_at':time.time(), 'last_voice_age_seconds':voice_age}
                    if not 0 <= voice_age <= .35:
                        print(json.dumps({'event':'fixture_not_barged', **trigger}), flush=True)
                        await completed_answer('phrase')
                        await wait_reply(started_phrase)
                        continue
                    started_utc = time.time()
                    print(json.dumps({'event':'fixture_barge_trigger', **trigger}), flush=True)
                    await speak(pcm)
                    await source.wait_for_playout()
                    started_phrase = time.monotonic()
                    print(json.dumps({'event': 'fixture_barge_sent'}), flush=True)
                    utterance += 1
                    await completed_answer('barge')
                    if os.environ.get('CALLER_WAIT_FULL_ANSWER') == 'private-jsonl':
                        started_phrase = time.monotonic()-1
                else:
                    await completed_answer('phrase')
                    if os.environ.get('CALLER_WAIT_FULL_ANSWER') == 'private-jsonl':
                        started_phrase = time.monotonic()-1
                await wait_reply(started_phrase)
            return 0

        if SCRIPT_WAVS:
            asyncio.create_task(scripted_phrases())

        engine = asyncio.create_task(voice.handle_call(
            audio_reader, writer, Broker(), config, {voice_call_id: prepared_call},
            hangup=fixture_hangup, answer_gate=fixture_answer_gate))
        try:
            await asyncio.wait_for(engine, timeout=MAX_SECONDS)
        except asyncio.TimeoutError:
            stop.set()
            audio_reader.feed_data(voice.pack_audiosocket_frame(voice.HANGUP_KIND, b""))
            await asyncio.wait_for(engine, timeout=voice.BackendConfig.from_environment().request_timeout + 15)
        await source.wait_for_playout()
        return 0
    finally:
        stop.set()
        if received_pcm:
            with wave.open(os.environ.get('CALLER_RECEIVED_WAV', '/tmp/caller_received.wav'), 'wb') as output:
                output.setnchannels(1); output.setsampwidth(2); output.setframerate(48000)
                output.writeframes(received_pcm)
            print(json.dumps({'event': 'received_audio_saved', 'pcm_bytes': len(received_pcm)}), flush=True)
        if crypto:
            await crypto.close()
        try:
            await api("PUT", state_full, {})
        except Exception:
            pass
        for task in list(media_tasks):
            task.cancel()
        if media_tasks:
            await asyncio.gather(*media_tasks, return_exceptions=True)
        if joined_rtc:
            try:
                await room.disconnect()
            except Exception:
                pass
        await source.aclose()


def main():
    try:
        return asyncio.run(run())
    except Exception as error:
        print(json.dumps({"event": "error", "reason": type(error).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
