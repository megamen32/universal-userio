# Спека: универсальные канальные адаптеры userio

Статус: черновик на ревью у владельца.
Дата: 2026-09-03. Автор цикла: L (Lead).
Якорь цикла: `Started at 2026-09-03T00:16:18+03:00 (system clock, TZ=Europe/Moscow)`.

## 1. Цель и результат

Один раз разработать универсальные адаптеры платформ обмена сообщениями — Telegram,
WhatsApp, VK, почта, SMS — и переиспользовать их во всех проектах. Первый
потребитель — AutoSellBot (AutoFindClient): его движок продаж должен работать
через библиотечный адаптер без изменения поведения.

Зафиксированные решения владельца:

- **Маршрут интеграции — библиотека + сервис.** Адаптеры живут как
  pip-модуль пакета `universal-userio`; проекты импортируют их in-process,
  сервис userio оборачивает те же адаптеры под контракт draft→approve.
  Альтернатива «всё через сервис userio по HTTP/MCP» отклонена: диалоговая
  петля продажника получает зависимость от аптайма сервиса и задержку,
  мульти-сессии Telethon пришлось бы уводить за сервис.
- **WhatsApp — через Node-мост WhatsApp Web (Baileys), как у Гермеса.**
  Живой образец: `scripts/whatsapp-bridge/bridge.js` из thirdparty/hermes-agent
  (`--port 30100 --session ~/.hermes/platforms/whatsapp/session --mode bot`),
  QR-паринг через `/opt/universal-inbox-whatsapp-qr`. Официальный Cloud API —
  запасная ветка, не сейчас.

## 2. Минимальный путь

- **Результат:** любые мои проекты общаются с любой платформой через один
  библиотечный контракт; AutoSellBot уже работает через него с Telegram.
- **Кратчайший канарей:** живой диалог AutoSellBot с тестовым получателем в
  Telegram идёт через `universal_userio.channels.telegram` (порт существующего
  `TelegramAPI`), поведение не изменилось.
- **Наименьший вертикальный срез:** ядро контрактов `universal_userio/channels/`
  + Telegram-адаптер + переключение импортов в AutoSellBot. Всё остальное
  (email, whatsapp, vk, sms, упаковка) — отдельные вертикали после канарья.
- **Discard list (сознательно не строим сейчас):** Bot API и Cloud API
  реализации, вебхук-ingress, очередь с ретраями и delivery reports в
  библиотеке, транскодинг медиа, мультиаккаунт-пул в сервисе, realtime-push в
  userio, новый UI дашборда.

## 3. Что уже есть (не изобретать)

| Платформа | Текущий механизм | Где живёт | Состояние |
|---|---|---|---|
| Telegram | `TelegramAPI(ChatPort, TelegramIdentityPort, TelegramModerationPort)`, Telethon/OpenTele2 | `AutoSellBot/telegram_api.py`, 597 строк | зрелый, боевой, in-process |
| Email | Himalaya CLI outbox для Gmail | `universal_userio/adapters.py` (`HimalayaGmailOutbox`) | работает, только отправка Gmail |
| VK | browser-worker: расширение `extensions/vk-inbox` + отправка из MAIN world | userio | работает, живой канарей записан |
| SMS | Android SMS Gateway (частный инстанс) | `universal_userio/adapters.py` (`AndroidSmsGatewayClient`) | работает, входящие + отправка |
| WhatsApp | Node-мост Baileys у Гермеса + QR-сервис | `~/.hermes/platforms/whatsapp/session`, порт 30100 | работает у Гермеса; в userio нет |
| ChatGPT | CDP MCP-адаптер | `universal_userio/adapters.py` | read-only, вне скоупа этой спеки |

Контракты сегодня двойные: богатый async `ChatPort` в
`AutoFindClient/contracts/chat.py` (11 операций + DTO + иерархия ошибок) и
тонкий sync `ChannelAdapter` в `universal_userio/contracts.py`
(list/read/download/send поверх store). Спека унифицирует их вокруг первого.

## 4. Архитектура (маршрут «библиотека + сервис»)

### 4.1 Ядро библиотеки

Новый пакет `universal_userio/channels/` внутри этого репозитория:

- `core.py` — контракты, переносом из `AutoFindClient/contracts/chat.py`
  без изменения формы (проверены в бою): DTO `ChatSummary`, `ChatMessage`,
  `DownloadedMedia`; ошибки `ChatOperationError`, `ChatPermissionError`,
  `ChatInvalidPeerError`, `ChatRateLimitError`; плюс существующий
  `AdapterNotSupported`.
- Protocol `Channel` (async) — форма `ChatPort`: `list_chats`, `read_chat`,
  `read_message`, `acknowledge_chat`, `download_media`, `send_message`,
  `forward_message`, `delete_message`, `edit_message`, `react`, `typing`.
- `capabilities: frozenset[str]` (`read`, `send`, `edit`, `delete`, `media`,
  `typing`, `react`, `forward`, `ack`) и `platform: str`. Операция вне
  capabilities → `AdapterNotSupported` (в MCP-слое уже мапится в
  «not supported by adapter»).
- Платформенные расширения живут рядом и не входят в ядро:
  `TelegramIdentityPort`, `TelegramModerationPort` — как в
  `AutoFindClient/contracts/telegram.py`.
- Инжекция транспорта: адаптер принимает готовый клиент
  (`TelegramChannel(client)`), а для автономного запуска — фабрика
  `from_env()`. Несколько инстансов адаптера = несколько аккаунтов
  (мульти-сессия AutoSellBot это уже требует).
- Зависимости — только через extras (сейчас `dependencies = []`, не ломать):
  `universal-userio[telegram]`, `[email]`, `[whatsapp]`, `[sms]`, `[test]`.

### 4.2 Сервис userio на тех же адаптерах

Сервис остаётся stdlib и синхронным. Тонкий `SyncChannelRunner`: один
фоновый поток с собственным event-loop на живой канал. Существующие
store-view адаптеры (`StoredChannelAdapter` для telegram/whatsapp/vk/mail)
остаются; живой адаптер подключается env-флагом вида
`USERIO_LIVE_TELEGRAM=1`. Контракт MCP/HTTP не меняется:
`channels.list/read/download/send_draft` + `draft.approve_send`.

Разделение ролей сохраняется: in-process потребители (продажник) шлют сами,
сервис userio — для human-in-the-loop (suggest/approve) и `auto_send`-правил.

### 4.3 Конфигурация и секреты

Только env/`.env`, одним шагом, никогда в коде или route-map (текущее правило
userio сохраняется):

- `USERIO_TELEGRAM_API_ID/API_HASH/SESSION/PROXY` (фабрика; AutoSellBot
  продолжает передавать свой клиент сам);
- `USERIO_SMTP_HOST/PORT/USER/PASS`, `USERIO_IMAP_HOST/PORT/USER/PASS`
  (или существующие Himalaya-переменные для gmail-бэкенда);
- `USERIO_WHATSAPP_BRIDGE_URL` (по умолчанию `http://127.0.0.1:30100`);
- `USERIO_SMS_GATEWAY_URL/TOKEN` — уже существует;
- VK-переменные расширения — по мере обёртки.

## 5. Адаптеры по платформам

### 5.1 Telegram — `channels/telegram.py` (первый, Ф1)

Транспорт: Telethon-совместимый клиент (OpenTele2 в AutoSellBot владеет
рантаймом — правило AutoSellBot/AGENTS.md сохраняется: сырые TL-запросы только
внутри адаптера). Перенос `AutoSellBot/telegram_api.py` как есть вместе с
telegram-спецификой (`parse_msg_url`, identity, moderation, membership).
AutoSellBot переключает импорты: `contracts/chat.py` становится реэкспортом
из `universal_userio.channels.core`, локальный `telegram_api.py` удаляется.
Канарей: живой диалог с тестовым получателем без изменения поведения
(существующий безопасный тестовый контур AutoSellBot).

### 5.2 Email — `channels/email.py` (Ф3)

Транспорт: два бэкенда — `himalaya` (существует, Gmail) и `smtp-imap`
(stdlib `smtplib`/`imaplib` в executor; любой ящик). Возможности: send, read,
media-вложения; edit/delete не поддерживаются — capabilities это декларируют.
Канарей: письмо на свой адрес + чтение ответа через канал.

### 5.3 WhatsApp — `channels/whatsapp.py` (Ф4)

Транспорт: HTTP-клиент к Node-мосту Baileys (паттерн Гермеса): `GET /messages`
(long-poll входящих), `POST /send`, `POST /send-media`, `GET /health`.
Мост — sidecar на аккаунт: `node bridge.js --port <p> --session <dir>
--mode bot`, QR-паринг через существующий `/opt/universal-inbox-whatsapp-qr`.
Копия моста закрепляется в `deploy/` userio, чтобы не зависеть от worktree
thirdparty. Канарей: сообщение тестовому контакту через мост + входящее
в `/messages`. Риск: ToS/бан номера — тестовый номер отдельный от личного.

### 5.4 VK — `channels/vk.py` (Ф5)

Транспорт: существующий browser-worker (расширение + MAIN-world send).
Обёртка над collect-каналом userio: входящие уже льются в store, отправка —
через существующий механизм воркера. Канарей: отправка тестовому peer +
чтение входящего через унифицированный `Channel`.

### 5.5 SMS — `channels/sms.py` (Ф5)

Транспорт: существующий `AndroidSmsGatewayClient`. Обёртка в `Channel`:
`inbound()` → read, `send()` → send_message; capabilities: `read`, `send`.
Канарей: SMS на тестовый номер через живой Android-гейт.

## 6. Фазовый план (каждая фаза — вертикаль со своим канареем)

Оценки — активные минуты, min/max, неизменяемы внутри фазы.

| Фаза | Содержимое | Канарей | Оценка |
|---|---|---|---|
| Ф0 | Спека + задача-запись (этот документ) | ревью владельца | сделано |
| Ф1 | Ядро `channels/` + перенос Telegram + переключение AutoSellBot | живой диалог продажника через библиотеку | 90–180 |
| Ф2 | Сервис userio на живом telegram-адаптере (`SyncChannelRunner`) | `send_draft`→`approve_send` реально отправляет TG-сообщение | 60–120 |
| Ф3 | Email: `smtp-imap` бэкенд | письмо туда-обратно | 45–90 |
| Ф4 | WhatsApp: клиент моста + sidecar на тестовом номере | сообщение тестовому контакту | 120–240 |
| Ф5 | VK + SMS обёртки | отправка+чтение по каждому каналу | 60–120 |
| Ф6 | Упаковка: extras, README-раздел, установка в другие проекты | pip install из другого проекта + импорт | 30–60 |

Порядок Ф3–Ф5 можно менять; Telegram первый, потому что AutoSellBot — первый
потребитель и эталон контракта.

## 7. Риски и открытые вопросы

- **WhatsApp ToS/Baileys** — баны номеров возможны; mitigation — тестовый
  номер, аккуратные тайминги, Cloud API как запасная ветка (не сейчас).
- **VK редизайны** — уже обходились (`data-itemkey`); живой механизм воркера
  остаётся источником правды.
- **sync-async мост в сервисе** — изоляция loop-потока от http.server; при
  проблемах Ф2 откатывается на store-view без потери остального.
- **Прокси Telegram** — фабрика `from_env()` обязана учитывать
  прокси-переменную (у AutoSellBot `TG_PROXY`).
- **Владение мостом** — закрепить копию `bridge.js` в `deploy/` userio;
  обновления thirdparty — осознанным diff'ом.

## 8. Проверка спеки

Self-review пройден: плейсхолдеров нет, маршруты не противоречат друг другу,
объём одной фазы реализуем за один цикл, каждая операция однозначно
принадлежит одной фазе и одному канарею.
