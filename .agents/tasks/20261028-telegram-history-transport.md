# Telegram fresh-history transport repair

Request: repair fresh Telegram history without losing stored messages. Lead owns
the combined backend release and consumer verification; this task owns only the
connector transport. No provider sends, calls, read acknowledgements, auth-key
copies or duplicate production clients were used.

## Root cause and discriminating proof

The connector reported TCP connection completion through HTTP CONNECT on
192.168.2.1:3128, but both remembered account sessions remained connecting.
Account-bound reconcile therefore refused reads with account not connected.
TCP completion was not an MTProto response or authorized account proof.

Key-free installed GramJS 2.26.22 probes to149.154.167.41:

| HTTP proxy port | Wire transport / Telegram port | Result |
| --- | --- | --- |
|3128|TCPFull/80|Closed without response after5017ms|
|3128|TCPObfuscated/443|Closed without response after5016ms|
|3127|TCPObfuscated/443|ResPQ, matching random nonce,100bytes,1055ms|

These probes use only plain req_pq_multi, not account credentials or sessions.
Official schema: tdlib/td/td/generate/scheme/mtproto_api.tl lines13,73 defines
resPQ#05162463 and req_pq_multi#be7e8ef1. Official Android
DrKLO/Telegram/TMessagesProj/jni/tgnet/Connection.cpp lines521-582 generates
the64-byte obfuscation header,EF tag and continuous AES-CTR stream.
Installed GramJS TCPObfuscated.js implements that transport. TelegramClient.js
line1081 supplies primary port80; useWSS is explicitly rejected with proxies
by telegramBaseClient.js. The supported custom connection class therefore
pins443 while keeping the existing session, sender and auth flow.

## Change and verification

- server.mjs selects the explicit TELEGRAM_TRANSPORT setting.
- transport-options.mjs preserves existing full mode by default, supports only
  explicit obfuscated mode, pins443 and rejects unknown mode values.
- Five focused tests pass: actual installed client construction uses443 with
  the HTTP proxy; full default preserved; invalid mode rejected; existing HTTP
  tunnel tests preserve coalesced data and prohibit direct-connect fallback.
- node --check server.mjs and scoped git diff --check pass.
- Installed server.mjs was byte-identical to published HEAD before this patch.
  No unrelated connector delta needs inclusion in the overlay.

Release configuration must edit only the existing Telegram connector process:
TELEGRAM_HTTP_PROXY_URL=http://192.168.2.1:3127 and
TELEGRAM_TRANSPORT=obfuscated. Shared proxy infrastructure remains unchanged.
Overlay server.mjs and transport-options.mjs, preserving existing dependencies.
No deployment is performed by this worker. A real fresh-history read through
the existing account-owning connector remains the Lead's release acceptance.
