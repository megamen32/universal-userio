# Telegram transport for the existing AgentCall engine

The transport, worker and UserIO call lifecycle are deployed on server100.
They are not yet accepted by a real Telegram recipient. Do not expose a working-call claim or
enable automatic incident calls until the connector seam and recipient canary
below pass. Matrix calls have already passed their separate acceptance.

## UserIO call lifecycle

`userio.calls.telegram.start` accepts `account_ref`, numeric `target`, natural
Russian `purpose`, stable `request_id` (16–128 characters), `confirm:true`,
optional `greeting` and `max_seconds` (30–600, default120). The caller must be
an enabled owner-bound Telegram account using its existing relay provider.
`userio.calls.telegram.get` reads the returned `call_id`; `.stop` ends that
same call. HTTP equivalents are `POST /v1/calls/telegram`,
`GET /v1/calls/telegram/<id>` and `POST /v1/calls/telegram/<id>/stop`.

Durable receipts survive API restart. Reusing the same request returns its
receipt; changed parameters conflict. Unknown provider outcomes remain unknown
and are never dialed again automatically. Stop during speech/DH preparation
prevents a new requestCall; final discard replies remain available during
teardown. Both audio tracks and their transcripts appear in the existing
Secretary conversation after the call, using the same authenticated playback.

The native CPython3.10 runtime is installed offline beside the main API with
`deploy/install-telegram-call-runtime.py`, the verified3.0.3 wheel and its SHA256.
It inherits the existing gateway's finite CPU/RAM/tasks budget and voice
configuration; the main API's Python environment and Telegram session remain
unchanged. Only one Matrix or Telegram native worker may run in that gateway.

## Ownership and runtime

`telegram_call_transport.py` uses the upstream `ntgcalls==3.0.3` native engine
for DH validation, key exchange, encrypted signalling and WebRTC. The worker
uses the installed `/usr/local/lib/agentcall/agentcall_realtime_bridge.py`,
`BackendConfig.from_environment()`, `CallController.prepare(..., repeat=1)`
and `handle_call(..., answer_gate=...)`. It adds no TTS/model/VAD configuration.

Install the prebuilt wheel in the existing UserIO Python runtime through the
owning deployment path; no source build is necessary on Linux x86_64/Python
3.10. The current UserIO runtime is Python3.14, so this diagnostic wheel is
not compatible with that runtime. A supported compatible native worker/dependency
installation remains required. The wheel imports in the diagnostic directory; no runtime
dependency was installed during implementation. `py-tgcalls` is not required.

The approved caller is the connected AI Secretary in Exmanager's existing
relay on30192. Its owner must expose the narrow account-bound call seam on
that same client. The personal connector on18095 is a different caller.
The existing GramJS connector owns the logged-in Telegram client throughout.
Never export/copy its StringSession into this worker, create a second client,
attempt login, or hand over the account's API hash/session keys.

## Minimal connector seam (integration owned by root)

Add an account-bound private call operation to the existing connector, using
its existing authorized `TelegramClient`. Before spawning one worker:

1. Verify caller account access and exact recipient via the same client's
   existing peer resolver. Calls are only to a positive private user ID.
2. Persist a durable `admission_id` and the fixed account/recipient/purpose.
   Claim it atomically before any `phone.requestCall`. Never replay a claimed,
   accepted, unknown or in-flight attempt, including after process restart.
3. Start one bounded `python -m universal_userio.telegram_call_worker`, inherit
   the gateway's live AgentCall configuration through the existing safe owner
   route, and keep stdin/stdout private. Do not log raw RPC traffic or secrets.
4. Forward account-bound phone updates from the existing client and execute
   only the five RPC methods below. Record the provider request receipt before
   delivering it to the worker. `phone.requestCall` is executed at most once.
5. On worker exit retain status/receipt/recording and release its finite job
   reservation. Reconciliation may read receipts; it must not originate again.

Initial stdin JSONL:

```json
{"call":{"admission_id":"owned-durable-attempt","user_id":"123456789","history_account_ref":"telegram:caller-account","peer_label":"Test recipient","purpose":"Authorized short test","greeting":"Привет. Это короткая проверка связи.","max_seconds":60}}
```

IDs here are examples. `admission_id` must come from the protected call ledger;
an arbitrary user-submitted string is not authorization. The account is bound
in the parent connector and cannot change through IPC.

Worker stdout RPC:

```json
{"event":"telegram_rpc","request_id":"1","method":"messages.getDhConfig","params":{"version":0,"random_length":256}}
```

Parent stdin reply:

```json
{"type":"rpc_result","request_id":"1","result":{"g":3,"p":"BASE64","random":"BASE64"}}
```

All bytes use strict base64; all MTProto `long` values use decimal strings to
preserve JavaScript precision. Parent converts keys to GramJS camelCase,
bytes with `Buffer.from(value, 'base64')`, and longs with its existing BigInt
mechanism. Do not pass normalized plain objects directly as TL constructors.

| RPC method | Input params | Normalized result |
|---|---|---|
| `messages.getDhConfig` | `version=0`, `random_length=256` | Full `g`, base64 `p`, base64 `random`; handle a not-modified constructor with the same client's verified cached DH config. |
| `phone.requestCall` | Fixed `user_id`, `random_id`, base64 `g_a_hash`, `protocol`, `video=false` | Unwrapped phone call: `type`, decimal `id`, `access_hash`, `admin_id`, `participant_id`. Persist this receipt first. |
| `phone.confirmCall` | `peer={id,access_hash}`, base64 `g_a`, decimal `key_fingerprint`, `protocol` | Unwrapped confirmed phone call: same IDs, `key_fingerprint`, `connections`, `protocol`, `p2p_allowed`, optional `custom_parameters` JSON string. |
| `phone.sendSignalingData` | Fixed `peer`, base64 encrypted `data` | Boolean result. |
| `phone.discardCall` | Fixed `peer`, `duration`, `reason=hangup/missed`, `connection_id="0"`, `video=false` | Normalize success to boolean; construct the appropriate TL discard reason. Forward any returned updates. |

`protocol` contains `min_layer`, `max_layer`, `udp_p2p`, `udp_reflector` and
`library_versions` taken directly from the installed native engine. Do not
guess or override these values.

Every `connections` entry keeps its official constructor fields:
`id`, `ip`, `ipv6`, `port`, optional base64 `peer_tag`, `turn`, `stun`, `tcp`,
`username`, `password`. Both legacy reflectors and WebRTC servers are passed
to NTgCalls as `RTCServer`; do not strip peer tags or relay credentials.

Normalize an RPC rejection as:

```json
{"type":"rpc_result","request_id":"1","error":true}
```

Store secret-safe owner diagnostics separately. No RPC is retried by the
worker. The parent must not retry outgoing call creation on a timeout.

Updates, from that same existing caller client, use the following JSONL:

```json
{"type":"telegram_update","update":{"type":"phoneCallAccepted","id":"11","access_hash":"22","admin_id":"333","participant_id":"123456789","g_b":"BASE64","protocol":{"library_versions":["13.0.0"]}}}
{"type":"telegram_update","update":{"type":"signaling","id":"11","data":"BASE64"}}
{"type":"telegram_update","update":{"type":"phoneCallDiscarded","id":"11"}}
{"type":"hangup"}
```

Subscribe before `phone.requestCall`, because acceptance can race its response.
Forward only the fixed caller/recipient's call or the persisted call ID.
`UpdatePhoneCallSignalingData.phoneCallId` maps to `update.id`.

## Audio and archive contract

Native capture and playback use external **48000 Hz, mono, signed 16-bit
little-endian PCM**. Incoming `on_frames(..., PLAYBACK, MICROPHONE, frames)`
passes `frame.data` to AgentCall AudioSocket kind `0x16`; outgoing AgentCall
kind `0x16` is split into two960-byte/10ms native frames,10ms apart.
Pinned NTgCalls3.0.3 consumes a fixed10ms buffer and ignores its input size;
passing a whole20ms packet loses its second half. Short native inputs are
rejected. AgentCall retains its cumulative20ms playback clock, including the
writer's10ms spacing; the adapter does not declare an SDK playback clock.
Neither direction is mixed into the other. AgentCall owns the separate
caller/assistant WAV tracks and transcript events; forward its broker events
to the existing UserIO recording/transcript pipeline.

The UUID AudioSocket frame and existing `handle_call` begin only after the
native `CONNECTED` event. An accepted invitation alone does not unlock speech.
Native failure, discard, IPC EOF, hangup, or max call duration terminates the
call. Buffers: updates32, concurrent signalling/RPC64, inbound PCM192KB/two seconds,
AudioSocket pending64KB, IPC line64KB. The existing bounded service profile
must also constrain process RAM/tasks/CPU and recording/temp storage.

## Source evidence

Checked on 2026-10-13, in the required order:

1. [Official MTProto schema](https://raw.githubusercontent.com/tdlib/td/master/td/generate/scheme/telegram_api.tl): `PhoneCall`, both connection constructors,
   `PhoneCallProtocol`, request/confirm/discard/signalling methods. Local
   `.tmp/matrix-calls-20261013/telegram_api.tl:1070-1080,3083-3090`.
2. [Official Android VoIPService](https://raw.githubusercontent.com/DrKLO/Telegram/master/TMessagesProj/src/main/java/org/telegram/messenger/voip/VoIPService.java): endpoint fields, encryption key direction, native engine creation.
   Local `.tmp/matrix-calls-20261013/VoIPService.java:3418-3473`.
3. [Official Desktop call implementation](https://raw.githubusercontent.com/telegramdesktop/tdesktop/dev/Telegram/SourceFiles/calls/calls_call.cpp): reflector/WebRTC endpoint conversion and tgcalls engine.
4. Installed Telethon1.45.0/layer229: `inspect.signature` confirms request,
   confirm, signalling and protocol constructor exposure. This is feasibility
   evidence, not a substitute for official client behavior.
5. [NTgCalls P2P implementation](https://raw.githubusercontent.com/pytgcalls/ntgcalls/master/ntgcalls/src/instances/p2p_call.cpp), [media manager](https://raw.githubusercontent.com/pytgcalls/ntgcalls/master/ntgcalls/src/media/stream_manager.cpp) and imported3.0.3 wheel docstrings confirm native exchange,
   external PCM and callbacks. Native cryptography is not reimplemented here.

## Verification and remaining acceptance

Fast unit suite: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
tests/test_telegram_call_transport.py`, expected2s, emergency20s. Covers
one-shot creation/unknown receipt, foreign receipt protection, early discard,
native connection gate, two PCM directions, fragmented AudioSocket, bounded
frames, and IPC EOF. It does not place a Telegram call.

Focused integration: two native local engines, no Telegram account or provider
action, real native DH/crypto/encrypted signalling and both nonzero PCM
directions through `TelegramCallTransport`. Opt in with
`USERIO_TEST_NATIVE_CALL_LOOPBACK=1`, install/import the pinned native wheel,
then run the exact test
`tests/test_telegram_call_transport.py::test_native_crypto_signaling_bidirectional_pcm_and_cleanup`.
Expected3s/emergency30s; the earlier admitted exploratory probe retained its
sealed100s emergency limit. Historical8k evidence, before48k framing repair:
`.tmp/telegram-call-transport-20261013/native-transport-test.txt` — tests2.30s,
overall2.59s, peakRSS72384KB, exit0. The direct paired-engine probe also exited0
in2.00s with45 threads, both inbound160-byte/10ms PCM frames and nonzero audio.

Earlier timeouts were a diagnostic-thread error: native methods return
`asyncio.Future`; `run_coroutine_threadsafe` requires a coroutine. Calling
native methods inside the owning loop fixed them. The transport uses that
correct route. A native teardown abort exposed callback reference cycles;
`close()` now removes native callbacks, drains/cancels its owned signalling,
stops media and releases the native engine before the Python loop closes.
The successful native integration checks that lifecycle too.

Those earlier results proved the local8k audio adapter; they do not prove a real Telegram
recipient or deployment. Inherited live shared cgroup at probe
time: memory.high121276817408, memory.max128849018880, swap.max1073741824,
pids.max1536, CPU quota4; parent verified ~43.6GiB available host RAM. No new
scope/daemon, dependency installation, or resource bypass was used.

Current48k verification: fast tests passed, including both distinguishable
native10ms halves and their cumulative10ms spacing. The paired native48k
probe failed before sending PCM, with media connection failure (14.24s test,
16.55s total, peak70720KiB, CPU affinity1, zero swap, exit1). That historical failure is retained. No provider/account call occurred. Evidence: `.tmp/telegram-call-transport-20261013/native48-result.json`.

Next acceptance: root integrates the existing connector's five-RPC seam and
durable admission, verifies the exact approved Telegram caller/recipient,
then performs one real answered call with bidirectional speech and UserIO
caller/assistant recording plus transcript display. The user explicitly
selected the connected AI Secretary calling their personal Telegram account;
resolve that approved recipient through the Secretary's existing client.

Changed caller-first focused integration on2026-10-14 PASSED1.98s: outgoing
engine starts before incoming, matching the successful direct probe. Both48k
native directions contained nonzero960-byte/10ms PCM; teardown passed. The old
incoming-first setup exhausted its own connection timeout before caller startup.
Same registered UserIO source profile, high=max512MiB/CPU1/tasks128/swap0/wall30,
peak80822272bytes and all OOM counters0; same-generation cleanup completed.
Evidence: `.tmp/telegram-call-transport-20261013/caller-first-case/` (payload.log,
native-result.json, completed-case receipt). The earlier bare-budget refusal
started no payload and was resolved through the existing own project profile.
This proves native transport locally; actual Secretary caller acceptance still
requires the installed existing-client seam and recipient dialogue.

Server100 installation on2026-10-14: published UserIO6d9bbd7 and gateway
e99dd010 installed; compatible offline native runtime imported3.0.3. Installer
completed in the supported own project profile, peak263266304bytes, swap/OOM0,
same-generation cleanup complete. Existing API/gateway budgets retained.
Actual authenticated MCP lists all three tools and refuses confirm:false before
provider activity. Actual private gateway launched its fixed worker and rejected
an invalid recipient with ValueError/EOF and zero RPCs, synthesis or dial.
Own source tests56PASS/1opt-in native skip; actual installed GramJS constructor
serialization7PASS; gateway routing4PASS. Real48k native proof is the separately
accepted caller-first run above. Private evidence:
`.tmp/telegram-call-runtime-install-20261014/`.

The existing Secretary relay seam is now installed (f84822d), with exact caller/
recipient capabilities200/configuredtrue and five RPCs, using the same session,
token and mounts. Account-scoped Secretary tools are installed (ac4224c), with
allowlist checks before start/get/stop and read-only limited to get.

One real UserIO MCP call attempt was accepted by Telegram, remained unanswered,
then ended with completed discard and CLOSED admission. No media connection or
archive was created. The initial TimeoutError result is retained privately;
source now distinguishes an unanswered invitation from transport failure.
Actual speech/recording acceptance is pending: the human requested no calls
while sleeping. No retry or wake-up is scheduled; resume the remaining dialogue
check only after the human returns. Never replay that accepted attempt.

Settled owner relay API on30192, existing protected bearer:
`POST /userio/telegram/calls/start` with admission_id/account_id/recipient_id/purpose;
`POST /userio/telegram/calls/<id>/rpc` with request_id/method/params → result;
`GET .../<id>/status`; `GET .../<id>/updates?after=<seq>` → bounded events/next_cursor;
`POST .../<id>/close` after worker teardown, no implicit discard. Caller is pinned
to the existing client generation; recovery never moves an active call to a new
client. This is the agreed seam, not evidence of its installation.
