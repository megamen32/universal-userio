---
name: vk-inbox-full
description: VK inbox extension — full feature (real-time all-chats capture, search, send), plus install into BrowserOS on localhost
---

# VK Inbox — full feature + BrowserOS install

## Запрос пользователя

> надо сделать чтобы расширение забирало и отправляло все чаты в режиме реального времени умело по ним искать и отсылать сообщения. короче было полноценным. Плюс+добавь в browseros на локалхосте его я там уже вошел в вк.

## Решения, зафиксированные с пользователем

- Send policy: расширение НЕ шлёт само. Готовит draft в UserIO, реальная отправка через существующий UserIO draft/approve pipeline (как адаптер Telegram/почты).
- History: всё что VK отрисовывает при открытии чата + кешируем в IndexedDB. Никакого агрессивного догруза через VK internal API.
- UserIO — канонический store и sender; расширение — VK Web DOM-адаптер.

## Бизнес-camary (Definition of Done)

1. Real-time: новые сообщения из ЛЮБОГО открытого чата попадают в IndexedDB за < 2 сек.
2. Search: popup находит сообщение по подстроке тела или имени собеседника за < 500 мс на 5k сообщений.
3. Send: из popup можно подготовить draft → отправить через UserIO `POST /v1/drafts/{id}/approve`. Текст появляется в VK после approve.
4. Установлено в BrowserOS на localhost:9223 — расширение активно, popup открывается, иконка в toolbar.

## Архитектура (расширение MV3)

```
extensions/vk-inbox/
├── manifest.json        # + service_worker, + indexeddb permission, vk.ru host
├── background.js        # SW: IndexedDB, UserIO bridge, message routing
├── content/
│   ├── chat-list.js     # vk.com/im — список чатов, real-time observer
│   ├── chat-view.js     # любой /im?sel=... — сообщения, input, send
│   └── sender.js        # userioVkSendDraft(peer, text) — пишет в input, click send
├── lib/
│   ├── db.js            # IndexedDB schema: chats, messages, drafts, queue
│   ├── userio.js        # fetch wrappers для /v1/messages, /v1/drafts
│   ├── selectors.js     # VK DOM selectors (с fallback-ами)
│   └── log.js
├── popup.html / popup.js / popup.css   # chat list + search + compose + send
├── options.html / options.js           # endpoint, token, account id
└── README.md
```

## Данные (IndexedDB)

- `chats`: `{peer_id, name, last_message_at, unread, last_preview}`
- `messages`: `{peer_id, msg_id, body, sender, ts, direction, status}`
  - индексы: `[peer_id+ts]`, `[body]` (lowercase)
  - status: `seen | captured | sent | failed`
- `drafts`: `{peer_id, body, created_at, draft_id?}` — после approve удаляются

## Поток

1. Content script (chat-list): MutationObserver на `.im-page--chats` → upsert chats + read counter
2. Content script (chat-view): MutationObserver на `.im-mess-list` → upsert messages
3. Background: при новом сообщении → POST /v1/messages (route_id=`vk-browser`)
4. Popup: search по IndexedDB cursor → список hits с peer/timestamp
5. Compose: background создаёт draft через UserIO (или расширение держит draft локально + approve), popup жмёт Send → POST /v1/drafts/{id}/approve → content script пишет текст в input + click send (как fallback если UserIO не принял)
6. Read markers: content script шлёт в background → POST /v1/inbox/seen

## BrowserOS install

1. Собрать extension (новая папка + обновлённый manifest)
2. Открыть chrome://extensions через browseros-cli
3. Включить Developer mode (toggle)
4. Click "Load unpacked" — откроется OS file picker
5. File picker: ввести путь к папке расширения (через xdotool / native input)
6. Verify: extension появилась в списке, нет ошибок

## Решения, требующие вашего ответа

- ✅ Send через UserIO draft/approve (ответ выше)
- ✅ History — DOM-only (ответ выше)
- ⏳ BrowserOS file picker — если native dialog проблемный, fallback: скопировать в `~/.config/BrowserOS/Default/Extensions/{generated_id}/` — потребуется рестарт. Выбираю UI-flow как primary.

## Оценка

Min: 30 min, Max: 90 min. Cycle = один vertical slice (capture + popup-list + search + compose + install).
