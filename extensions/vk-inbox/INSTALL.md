# VK Inbox sidecar — install

This extension (**Universal UserIO Agent**, v0.3+) runs **inside the operator's
own Chromium-based browser** (Chrome, Chromium, BrowserOS, Brave, Edge, Arc),
uses the already-logged-in VK Web session to capture chats and send messages
through UserIO, and doubles as a universal site-data collection agent (see
section below). It does **not** store or transmit VK cookies, tokens, or any
auth material — it only reads the live DOM of `vk.com` / `vk.ru`, the local
IndexedDB, and the response bodies of tasks published by your UserIO server.

UserIO never sees your browser session. The UserIO HTTP API only sees the
captured message envelopes and the manual `send` requests; nothing more.

## 1. Pick a browser

Any Chromium 120+ based browser. The same extension works in all of them.
Pick the browser where you already have VK Web open and logged in.

Common profiles:

- BrowserOS (recommended for headless / agent-driven workflows):
  `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
  select `extensions/vk-inbox/`.
- Regular Chrome / Brave / Edge: same flow. Use a dedicated profile if you
  want to isolate the extension from personal browsing data.

## 2. Build the static zips (only if you changed the source)

```bash
cd extensions/vk-inbox
./scripts/build.sh
# Outputs:
#   ../../src/universal_userio/static/vk-userio-extension-mv3.zip   (MV3, Chrome/Chromium)
#   ../../src/universal_userio/static/vk-userio-extension.zip       (legacy MV2 alias)
```

If you do not have `zip` installed: `apt-get install -y zip` (Debian/Ubuntu) or
`brew install zip` (macOS). The script exits non-zero on missing dependencies.

## 3. Load the extension

For a local checkout (developer flow):

1. Open `chrome://extensions` in the target browser.
2. Toggle **Developer mode** (top-right).
3. Click **Load unpacked** → select the directory `extensions/vk-inbox/`.
4. Chrome will compute the extension ID from the absolute path. Take note of it
   (you will see it under the extension card) — it stays stable as long as the
   folder does not move.

For a packaged install via UserIO's HTTP server:

```
http://127.0.0.1:18093/vk-userio-extension-mv3.zip    # MV3
http://127.0.0.1:18093/vk-userio-extension.zip        # legacy MV2 alias
```

Download one of the zips, unzip it somewhere stable, then **Load unpacked**
that unzipped directory.

> Do **not** "Load unpacked" a `.zip` file directly — Chrome expects a folder.

## 4. Configure the connector

Click **Сведения** (Details) → **Параметры** (Options) on the extension card, or
right-click the extension icon → **Options**. Set:

| Field           | Default                       | Notes |
|-----------------|-------------------------------|-------|
| UserIO endpoint | `http://127.0.0.1:18093`      | Loopback only — the extension talks to a UserIO server on the same machine. |
| UserIO API token | *(empty)*                     | Only if your UserIO is configured with `USERIO_API_TOKEN`. Stored in `chrome.storage.local`. |
| `route_id`      | `vk-browser`                  | Must match the entry you registered in `USERIO_ROUTES_JSON`. |

## 5. Verify the canary

1. Open `https://vk.com/im` in the same browser profile (or `https://vk.ru/im`).
2. Open at least one conversation.
3. Click the extension icon → **Universal UserIO Agent** popup should show:
   - **Чаты** tab: the captured chats with last preview.
   - **Поиск** tab: typing a substring of any visible message returns hits.
   - **Отправить** tab: enter a `peer_id` and text, click **Отправить через VK Web**.
     The message appears in the open VK Web chat; the local IndexedDB has it as
     `direction: "out"`, `status: "sent"`.
   - Footer: `чатов: … · сообщений: …` and the `сбор: …` status line.
4. From the UserIO side, query `GET /v1/inbox` (with bearer if configured) — new
   captures appear there within seconds.

## Universal site collection (v0.3+)

The extension polls your UserIO server once a minute for collection tasks and
executes each due task as a real browser `fetch` — with the user's own session
cookies when the recipe sets `credentials: "include"`. This is how you "connect
another site" without writing any per-site code: publish a task, and every
installed agent reports the response body back.

1. Publish a task on the server: edit
   `/var/lib/universal-userio/collect-tasks.json` (or the path configured via
   `USERIO_COLLECT_TASKS_FILE`). Start from
   `extensions/vk-inbox/collect-tasks.example.json`:

   ```json
   [
     {
       "id": "sitecart-orders",
       "title": "SiteCart orders",
       "site": "https://admin.sitecart.ru",
       "active": true,
       "every_sec": 300,
       "recipe": {
         "kind": "fetch",
         "url": "https://admin.sitecart.ru/api/orders?limit=20",
         "method": "GET",
         "headers": {"Accept": "application/json"},
         "credentials": "include",
         "response": "json"
       }
     }
   ]
   ```

2. Make sure the browser profile is logged in to that site (for
   `credentials: "include"`); for public APIs use `credentials: "omit"`.
3. Trigger a run immediately with the **↻** button in the popup footer, or wait
   for the next alarm (the extension polls every minute; a task runs at most
   once per `every_sec`, default 300).
4. Read results from the server (any UserIO bearer token):

   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     "http://127.0.0.1:18093/v1/collect/results?task_id=sitecart-orders&limit=10"
   ```

   Each result is a `universal.collect.result.v1` envelope with `status`
   (`ok`/`error`), `http_status`, `data` (parsed JSON or raw text), and the
   `user`/`agent` that produced it. Results are also appended to
   `/var/lib/universal-userio/collect-results.jsonl`
   (`USERIO_COLLECT_RESULTS_FILE` overrides).

Trust boundary: tasks come only from your UserIO endpoint and results go only
back to it; site cookies and tokens never leave the browser profile.

## 6. Updates

After editing the source, run `./scripts/build.sh` again. The UserIO HTTP API
serves the new zip at the same URL. To pick up the update in the browser:

- `chrome://extensions` → click **Reload** on the extension card. No need to
  re-install.
- Or, if the extension ID changed (folder moved), re-load unpacked.

## Troubleshooting

- **Popup shows "Нет данных"**: the extension SW just started; refresh the
  popup. The current build retries twice with 600ms delay.
- **"Receiving end does not exist" on send**: the target VK tab has no content
  script — reload the tab so `chat-view.js` registers the `sendText` listener.
- **"Could not establish connection"**: the target VK URL doesn't match any
  matcher in `background.js#sendViaVK`. The current matchers are
  `?sel=<peer>`, `/im/convo/<peer>`, `/im/<peer>`. Add a new matcher if your
  VK URL pattern is different.
- **Send button click fails**: VK redesign 2026 toggles the same DOM element
  between "mic" and "send" depending on composer state. `lib/selectors.js`
  includes `.ConvoComposer__sendButton`, `.ConvoComposer__sendButton--mic`,
  and `button svg[class*="send_24"]` — extend this list if VK renames.

## Uninstall

`chrome://extensions` → **Remove**. Optionally delete the IndexedDB:

```js
// in DevTools console on any VK page:
indexedDB.deleteDatabase('userio_vk');
```
