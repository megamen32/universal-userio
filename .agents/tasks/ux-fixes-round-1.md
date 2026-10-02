# UX fixes round 1 — Messenger

Status: in_progress
Started at: 2026-09-04T03:13+0300
Lead: roomhacker
Canary: rebuild static, load chat list in browser, see per-platform icons + non-sticky draft + better initials + contextual empty state.

## Context

Dasha review found 25 complaints in the Universal UserIO messenger UI. After
recon, most of the "headline" issues (raw ids instead of names, datetime with
seconds under every message, raw `[WhatsApp image]` text, raw HTML email body,
"No chats in this channel." empty state, missing outbound/inbound distinction)
are **already fixed in the current source** by commit `ddc4b79 feat: contact
names, Telegram-style chat UX, Russian UI`. The build served on `127.0.0.1:18098`
during the review was stale — the live tab no longer loads (`curl: refused`).

So the surviving real bugs in current source are smaller but still
user-visible:

A. **Per-platform icons**: `channelIcon()` (App.tsx:31) returns
   `<MessageCircle />` for every non-gmail source, so VK / WhatsApp / Telegram
   show the same glyph. Dasha: "у Vk и Whatsapp одинаковая иконка (облачко)".
B. **Sticky draft**: `const [draft, setDraft] = useState("")` is app-level
   (App.tsx:124). Switching chats carries the text. Dasha typed "привет!"
   in one chat, opened another, draft still there.
C. **Initials for phone-like senders**: `initials("+79103332444")` returns
   `"+"` (single leading character). Visually ugly.
D. **Search empty state lacks filter context**: shows the same "Ничего не
   найдено" whether you search or filter a platform. Better: mention the
   active query and platform.

## Discard list (intentionally not in this slice)

- Avatar images / contact photos — needs server-side upload.
- Reading / delivered ticks — message model has no `direction` field today;
  would need a backend change.
- Inline rendered HTML email preview with subject — already there.
- Mobile swipe gestures — separate UX work.
- Send button label / icon tooltip — low impact, separate round.
- Backdrop dim bug — works in source; Dasha's screenshot caught an in-flight
  transition.
- Per-message timestamps with seconds — already `HH:MM` in source.
- Auto-resizing textarea — separate component change.

## YAGNI slices

Each slice is small enough to commit independently and verify in the browser
between commits.

### Slice A — per-platform icons
- Edit `channelIcon()` to return Telegram / VK / WhatsApp / Gmail specific
  lucide icons (`Send` for Telegram, already used in nav; `MessagesSquare`
  for VK; `Phone` for WhatsApp; `Mail` for Gmail — already there).
- Edit sidebar (App.tsx:237) where `channelIcon(platform)` is rendered to
  pass the platform through.
- Rebuild static.
- Verify: open menu, see 4 different icons.

### Slice B — clear draft when switching chats
- Reset `draft` inside `openChat()` (App.tsx:175) and when
  `selectedChat` changes through `loadConversation` success.
- Rebuild static.
- Verify: type in chat A, open chat B, field is empty.

### Slice C — better initials for phone-like senders
- Adjust `initials()` to strip leading `+` and use first 1–2 digits when the
  whole input is digits/`+digits`.
- Rebuild static.
- Verify: list shows "79" or "7S" for `+79103332444` instead of "+".

### Slice D — search empty state with context
- When `search` is non-empty: show "По запросу «X» ничего не найдено".
- When `selectedChannel !== "all"` and `search` is empty: show "В этом канале
  чатов нет".
- Else: keep existing "Ничего не найдено".
- Rebuild static.
- Verify: type gibberish in search, see contextual message.

## Verification recipe

For each slice:
1. `cd web/universal-userio-web && npm run build` (writes to
   `src/universal_userio/static/`).
2. Reload tab in MCP chrome (`pageId=3` still cached), navigate to the
   surface that proves the fix.
3. `cd ~/agents-projects/universal-userio && python3 -m pytest -q` — no
   frontend tests, but ensures backend didn't break.

## Out of scope (next rounds)

- Round 2: contact photos + group author labels.
- Round 3: inline send/read indicators + per-message delivery ticks.
- Round 4: archive / pin / mute on chat list.
- Round 5: attach file / emoji / voice in composer.
