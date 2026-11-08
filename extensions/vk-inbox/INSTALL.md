# VK Inbox sidecar — install

This extension (**Universal UserIO Agent**, v0.3+) runs inside the operator's
existing Chromium profile (Chrome, Chromium, Brave, Edge, or Arc). It uses the
already-authenticated VK Web session to capture chats and send messages through
UserIO, and doubles as a universal site-data collection agent (see section below).
It does **not** store or transmit VK cookies, tokens, or any
auth material — it only reads the live DOM of `vk.com` / `vk.ru`, the local
IndexedDB, and the response bodies of tasks published by your UserIO server.

The VK browser session stays in Chrome. ChatGPT cookies are transferred to
UserIO only through the separate account choice and explicit confirmation
described below. The UserIO HTTP API receives captured message envelopes and
manual `send` requests.

## 1. Choose the logged-in Chrome profile

Use the Chromium profile where VK Web is already signed in. Install the extension
once in that profile through Chrome's `chrome://extensions` page. Agent Browser
then controls the same profile for automated reads and checks:

```bash
agent-browser --session vk-userio --profile Default open https://vk.ru/im
agent-browser --session vk-userio snapshot -i
```

Use the actual Chrome profile name or path when it is not `Default`.

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
| UserIO endpoint | `http://127.0.0.1:18093`      | HTTP разрешён только для loopback; удалённому UserIO нужен HTTPS. |
| `route_id`      | `vk-browser`                  | Must match the entry you registered in `USERIO_ROUTES_JSON`. |

In extension settings click **Войти в UserIO**. Complete login and consent in
the UserIO window. The account shown by UserIO is the one being authorized;
the extension verifies the issued access by reading its inbox. The shared
download contains no user token.

ChatGPT is connected separately: inspect the profile account first, then
explicitly confirm sending its cookies to UserIO.

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

### Обновление на macOS одной командой

Скрипт `scripts/install_macos.py` находит существующее расширение по профилю
Chrome, сохраняет ZIP-резерв и обновляет его файлы, сохраняя локальный
`lib/config.js` и хранилище профиля. Он не принимает архив другой программы,
смену ключа расширения, выход файлов за папку и понижение версии.

```bash
python3 install_macos.py --archive ~/Downloads/Universal-UserIO-Agent-0.9.11.zip
```

Без `--archive` используется опубликованный пакет
`https://msg.bezrabotnyi.com/vk-userio-extension-mv3.zip`. Дополнительные параметры:
`--profile "Profile 1"`, `--target /полный/путь/к/папке`, `--no-reload`.

Скрипт просит уже установленное расширение выполнить `chrome.runtime.reload()`
через разрешённый Chrome интерфейс Apple Events. Если Chrome отказал или
расширение выключено, открывается `chrome://extensions` для обычной перезагрузки.
Завершение копирования и принятие запроса перезагрузки сообщаются отдельно;
рабочую версию подтверждает ответ расширения в UserIO. Первый запуск нового
профиля требует обычного добавления распакованной папки и входа в UserIO.
