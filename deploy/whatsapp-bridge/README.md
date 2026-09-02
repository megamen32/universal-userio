# WhatsApp bridge (pinned copy)

This is a pinned copy of the Hermes Agent WhatsApp bridge
(`scripts/whatsapp-bridge/bridge.js` from thirdparty/hermes-agent, snapshot of
2026-09-03).  It connects to WhatsApp Web through Baileys and exposes a
loopback HTTP API for the universal channel adapter
(`universal_userio.channels.whatsapp`).  The running Hermes instance stays the
reference deployment; refresh this copy deliberately by diffing against
upstream.

## Run (one bridge per WhatsApp account)

```bash
cd deploy/whatsapp-bridge
npm ci
node bridge.js --port 30101 --session /var/lib/userio/whatsapp/session --mode bot
```

- `--session` is a private directory holding the Baileys credentials
  (`creds.json`); never commit or copy it.
- Pair a new number by QR: reuse the existing local service
  `/opt/universal-inbox-whatsapp-qr`, or watch the bridge console QR on first
  start.
- Use a dedicated test number for canaries; ToS risk of bans is real.

## HTTP contract (loopback hosts only)

- `GET  /health`  → `{"status":"connected", ...}`
- `GET  /messages` → drain queued inbound Baileys events (JSON array)
- `POST /send` `{chatId, message, replyTo?}` → `{messageId, messageIds}`
- `POST /edit` `{chatId, messageId, message}`
- `POST /typing` `{chatId}`
- `POST /send-media` `{chatId, filePath, mediaType?, caption?, fileName?}`
- `POST /send-poll`, `POST /send-location` — see bridge.js header

Point the Python adapter at it with
`USERIO_WHATSAPP_BRIDGE_URL=http://127.0.0.1:30101`.
