# UserIO: личная отправка Telegram — ремонт и установка

Поручение: codex `01a1443d`, brief
`exmanager/.agents/tasks/briefs/20261016-userio-personal-send-repair.md`.
Маршрут сессии: `minimax-coding-plan/MiniMax-M3.1-Flash-Preview`, без fallback.
Ремонт личной отправки UserIO; X-manager и его поручение не трогались.

## Подтверждённая первопричина

`userio_draft_approve_send` для `conv_c7b6300e97fa96bb0e4d789e`
(`telegram:540308572` → peer `8810909089`) вернул
`telegram-qr delivery failed: HTTP 404 {"error":"account telegram:540308572 is not connected"}`
и оставил `draft_3e0e247a3d774795b50f9f9a30ba686a` в `sending`.

Живой разбор (только чтение, без отправки и без правки БД):

1. Коннектор жив, но не имеет ни одного live slot: `/folders?account_id=…`
   отвечает `requested account is not connected` и для `telegram:540308572`,
   и для `telegram:8810909089`.
2. `docker logs userio-backend`: `sync account-1 error: session is not authorized`
   каждые 30 секунд. GramJS `isUserAuthorized()` глушит настоящую ошибку.
3. Прямой зонд `updates.getState` на копии сохранённой сессии (в контейнере,
   файл сессии не перезаписывался, `apiId/apiHash` из штатного age-ключа,
   тот же `TELEGRAM_HTTP_PROXY_URL`):
   `AUTH_KEY_UNREGISTERED` для `updates.getState` и `getMe`.
   Сессия `telegram-qr:account-1` отозвана Telegram и больше не авторизуется.
4. В БД `provider_accounts`: `telegram:540308572` → `telegram-qr:account-1`,
   `telegram:8810909089` → `telegram-qr:account-2`; файла `account-2.session`
   нет (только `.disabled`), поэтому личный маршрут отправки целиком зависел
   от отозванной сессии.
5. В `drafts` было 5 записей в `sending` — дефект системный, не только для
   этого черновика.

История при этом читается: её присылает штатный сборщик exmanager, а не
коннектор UserIO. Это не доказательство живой авторизации.

## Что было не так в коде

1. `TelegramQrHttpOutbox.send_reply` поднимал на любой `HTTPError` обычный
   `RuntimeError`. `UserIOService.approve` отпускает durable-claim только на
   `DeliveryNotSubmittedError`, поэтому отказ коннектора «до входа в Telegram»
   считался неопределённым исходом: черновик навсегда оставался в `sending`,
   повторное одобрение блокировалось текстом «Результат отправки уточняется»,
   а владелец не получал никакого действия.
2. Коннектор отдавал тот же 404 без кода причины и повторял попытки каждые
   30 секунд, включая безнадёжный отозванный ключ.
3. Тот же дефект был в `POST /v1/workspace/triage/<seq>/send`: `send()` без
   обработки `DeliveryNotSubmittedError` оставлял и draft-claim, и
   `send_state='sending'` навсегда.

## Изменения

| Файл | Изменение |
|---|---|
| `deploy/telegram-qr-connector/session-auth.mjs` | новый модуль: прямой `updates.getState`, классификация отозванного ключа против временной недоступности, backoff терминального отказа |
| `deploy/telegram-qr-connector/server.mjs` | sync-loop использует классификацию, статус `relogin-required`, backoff 900с вместо 30с; `/send` отдаёт `code` + `submitted:false`, а неожиданная ошибка — `submitted:"unknown"`; маршрутизация по точным `account_id`+`slot` без отката на другой аккаунт |
| `deploy/telegram-qr-connector/ingress-state.mjs` | причина отказа видна в панели аккаунтов |
| `src/universal_userio/adapters.py` | `TelegramConnectorRefused(DeliveryNotSubmittedError)`; 4xx коннектора = доказанная неотправка, 5xx/сеть = неопределённость; понятное владельцу сообщение по `code` |
| `src/universal_userio/service.py` | отправка личного Telegram передаёт закреплённый слот; triage-путь отпускает оба claim на доказанной неотправке |
| `src/universal_userio/store.py` | `release_workspace_triage_send` — точная отмена только своего claim |
| `src/universal_userio/relay_account_bridge.py`, `channels/live_telegram.py` | новый параметр `slot` в общей сигнатуре |
| `tests/telegram_session_auth.test.mjs` | 11 тестов: классификация, зонд, форма ответа `/send`, границы identity |
| `tests/test_telegram_qr_outbox.py`, `tests/test_telegram_personal_send_state.py` | 11 тестов: refusal→`proposed`, 5xx→`sending`, повторное одобрение невозможно, слот в payload |

Итог: 29 python + 70 node в затронутых наборах зелёные. Полный прогон
`pytest tests` даёт те же 29 падений, что и на чистом HEAD (проверено на
копии исходников HEAD): нужен Python 3.12+ для native FastAgent и отдельно
сломанные MAX/telemost наборы. Регрессий нет.

## Состояние старого черновика

`draft_3e0e247a3d774795b50f9f9a30ba686a` оставлен ровно в `sending`
(неопределённый исход), как потребовал владелец: не повторялся, не отменялся,
не удалялся, БД вручную не правилась. Новый код не может улучшить его
состояние задним числом — claim был снят до появления классификации. Реальный
provider outcome сверен штатным read-only зондом: `AUTH_KEY_UNREGISTERED`,
сообщение уйти не могло.

## Canary

Отдельный точный canary в разрешённом owner-диалоге через настоящую поверхность
владельца (MCP UserIO, тот же Bearer), не через тест:

1. `userio.draft.create` в `conv_c7b6300e97fa96bb0e4d789e` с телом-маркером
   «Канарейка ремонта личной отправки UserIO 16.10.2026…» →
   `draft_397b38a3f0e14eea89daf28828e78c01`, `status: proposed`.
2. `userio.draft.approve_send` с `confirm=true`, точным текстом и
   `expected_chat_id=conv_c7b6300e97fa96bb0e4d789e` →
   `Telegram отозвал вход этого аккаунта. Переподключите его в UserIO —
   сообщение не отправлено, черновик можно отправить снова.`
   До ремонта тот же вызов давал «Результат отправки уточняется» и оставлял
   черновик в `sending` навсегда.
3. Readback состояния БД (снимок через sqlite backup, без записи):
   canary → `proposed`, `outbox_receipt` пуст; `approved` без изменений (229),
   `sending` без изменений (5). Ничего не принято и никуда не отправлено.
4. `userio.draft.delete` для canary: `deleted: true`, `proposed` вернулся к 343.

Прямой ответ коннектора на тот же exact account+slot:
`{"error":"account telegram:540308572 is not connected","submitted":false,
"code":"account_relogin_required"}` HTTP 404.

## Установка

Живой backend-контейнер UserIO **не** управляется Compose (у него нет
compose-меток), поэтому `docker compose up` падал с конфликтом имени. Активация
собрана из репозиторного скрипта по образцу штатного релиза
`20261016-meeting-release.py`, но с пересозданием контейнера:

- `scripts` → `.agents/tasks/20261016-personal-send-release.py`: тонкий слой
  `FROM universal-userio-meeting:telemost-live2` + только 8 опубликованных
  объектов, сборка в заявленном бюджете (1GiB RAM, CPU2, pids128,
  `--network=none`, `DOCKER_BUILDKIT=0`, 6.2с) и самопроверка содержимого
  образа по SHA256 до любой активации.
- `.agents/tasks/20261016-personal-send-activate.py`: та же конфигурация
  контейнера, что и живая (22 bind, память 3GiB/swap 3.25GiB, host-сеть,
  ulimit, log-opt), новый образ, health через штатный
  `container_runtime --health`, сверка хешей и конфигурации, автоматический
  откат (контейнер+compose-конфиг) при любой неудаче.

Два провала откатились сами и были причиной правки, а не скрытых проблем:
первый — неверный путь `COPY` для вложенного файла, второй — сравнение
`Binds` как строки вместо множества (Docker нормализует порядок).

Итог установки:

| | |
|---|---|
| образ | `universal-userio-personal-send:29b4144a2596` (8/8 файлов = git HEAD) |
| контейнер | `fd818c0bd27c` |
| предыдущий | контейнер `userio-backend-prev-20261016` (Exited) и образ `universal-userio-meeting:telemost-live2` — готовый откат |
| compose-конфиг | `backend.image` обновлён на новый образ |
| health | `container_runtime --health` = 0, все 7 процессов started, `/v1/accounts` 401 (живой), frontend 200 |

Два замечания по гигиене: внутри остановленного предыдущего контейнера остался
read-only диагностический скрипт `tg-auth-probe.mjs` (в новый образ он не
попадает), а в compose-конфиге до ремонта стоял устаревший
`universal-userio-meeting:24cd36a3e0f7` — теперь он совпадает с живым образом.

## Остался человеку

Аккаунт `telegram:540308572` (и `telegram:8810909089`, чья сессия тоже
отсутствует) нужно войти заново через обычный QR/телефон в UserIO. Кодом это
не лечится: Telegram отозвал ключ. До входа личная отправка невозможна, но
теперь она честно отвечает «переподключите аккаунт» и не оставляет зависших
черновиков.

Чужой WIP не тронут и не закоммичен: 4 строки в `src/universal_userio/messenger.py`,
untracked `src/universal_userio/messenger_telegram_mirror.py`, hunk
`postedIds/message_ids` в `server.mjs`, `.planning/`, `graphify-out/`.
Активных сессий по этим путям в Agent Herder нет.