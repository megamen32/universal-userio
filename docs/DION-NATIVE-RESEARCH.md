# DION native implementation research

The independent upstream `kulikov0/whitelist-bypass` repository exposes the
headless call pieces under `relay/dion` and `relay/headlessapi`. The useful
provider seam is concrete: `dion.Session` owns authenticated HTTP, refresh,
`ConnectWSS`, event lookup/create, and `dion.Call` owns signaling plus Pion
media. UserIO must copy or depend on that upstream implementation directly,
not WhiteTransport.

Observed upstream endpoints include `/platform/v1/auth/refresh/web`,
`/platform/v1/whoami`, `/conference/v1/events/slug/{slug}`,
`/conference/v1/events`, and `/conference/v1/connect/wss`. These are conference
surfaces, not personal-chat endpoints.

## Personal chat evidence (2026-11-26)

The authorized Mac HAR was inspected in place, without copying it, exporting
credentials, or printing headers/bodies containing account data. Its static
web-client asset `/mf/chats/static/js/async/6173.a3920354.js` defines:

- `POST https://chats-api.dion.vc/v1/crypto/sessions` with
  `{client_public_key}` and `Dc-Encode-Codec-Type: b64`.
- The handshake response has `session_id`, `server_public_key`, `valid_after`,
  and `valid_before`; private key material is generated locally, not imported.
- ECDH P-256, raw uncompressed public-key base64; HKDF-SHA256 with empty
  salt/info; AES-256-GCM with 12-byte random nonce and 128-bit tag.
- JSON envelopes: `{encrypted: base64(nonce || ciphertext || tag)}`.
- Request headers: `Dc-Crypto-Session-Id`, `Dc-Encoded-Data-Over-Json: true`,
  and `Dc-Encode-Codec-Type: b64`. The frontend re-handshakes on HTTP 428.
- `GET /v2/conversations` defaults to `limit=50`.
- `GET /v1/conversations/{id}/history`, `GET /v1/messages/{id}`,
  `GET /v1/conversations/{id}/intermediate?ids=...`.
- `POST /v1/messages/batch` is the client send route; do not wire it until
  durable outbox and uncertainty reconciliation are integrated.
- `GET /v1/connect/wss` returns an encrypted response. WebSocket event names,
  history query parameters, paging termination and message schema still need
  extraction; do not assume the provisional normalized parser is a wire schema.

`dion_chat_crypto.py` implements the observed crypto, with an independent Node
WebCrypto interoperability test and tamper rejection. It makes no requests and
does not refresh credentials. An auth owner supplies access credentials later.

## Upstream call caveats

At inspected upstream revision `d1c48c06c0abe64ce30f404cddd54989edddaa70`,
`Call` is a VP8 data tunnel, not a ready audio bridge. `handleSpeakerJoined`
actively kicks previously known peers; do not use that high-level call wrapper
in an ordinary meeting. Reuse low-level signaling/Pion pieces instead. Also,
`Session.DoAuthenticated` can refresh/retry internally: a helper must never
receive the backend refresh chain or independently run this refresh path.

`tools/dion-call-helper` is the initial UserIO-owned process boundary. It
validates request identity and fixture-only create/join/leave actions, but does
not contact DION yet. This prevents accidental real calls while the upstream
dependency and UserIO call lifecycle are wired. The next implementation step
is to replace the explicit unavailable result with a low-level upstream adapter and
retain the fixture gate in tests.
