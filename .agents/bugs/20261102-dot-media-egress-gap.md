# Dot to Telegram images were silently dropped

**Observed:** 2026-11-02. **Status:** source fix staged in a separate review branch; no production release or live send.

## Confirmed defect
- dot-browser-page.mjs returned alt names of visible image attachments, but not a stable source identity or downloadable image bytes.
- dot-mirror.mjs hashed only text, held image-only messages as no_visible_text and forwarded captions without their images.
- server.mjs injected only sendMessage, not the connected Telegram bot client's sendFile.

## Narrow source repair
- Keep one existing Telegram connector client, its protected owner/bot/room binding, durable mirror journal and no-replay semantics.
- Snapshot an exact room message and hash image source URLs in the browser without publishing their contents.
- Fetch only same-origin, browser-visible JPEG/PNG/WebP image bytes into a bounded temporary browser buffer; export via limited native MCP chunks and verify SHA-256 in Node. No cookie/token reads, arbitrary URL loading or new browser/process.
- Validate 8 MiB per image, 20 MiB per album, ten images; synthesize safe filenames and use GramJS CustomFile plus the already connected client's sendFile.
- Preserve native reply target, short literal captions and all parts of long text.
- Persist original source message identity, image digests and send_uncertain BEFORE each provider send. UNKNOWN is not replayed; media edits require review. Accepted is not delivery.
- If the original source is inaccessible or changes during download, hold and block, never silently send only caption.
- No new claim table/worker/fence/ACL. The backend claim owner must provide canonical source identity and fence for this existing transport boundary before integration.

## Exact file scope
Only deploy/telegram-qr-connector:
- dot-browser-page.mjs, dot-browser-mcp.mjs, new dot-browser-egress.mjs and its test.
- New dot-egress.mjs and its test; existing dot-media.mjs unchanged.
- dot-mirror.mjs and its test, dot-relay.mjs, server.mjs.

## Verification and remaining blockers
Synthetic browser DOM, HTTP stream, and fake native Telegram client. No messages or account credentials. Baseline 88 tests, plus regressions for photo-only, captions, album, reply, restart, changed media, wrong origin, oversize, corrupted digest and UNKNOWN. Actual delivery and provider readbacks remain blocked until the dot room loads and shared claim/fence contract is reviewed. Unsupported cross-origin images fail closed. Review, integrate with the current guarded release only after coordination; run bounded authorized E2E after browser blocker clears.
