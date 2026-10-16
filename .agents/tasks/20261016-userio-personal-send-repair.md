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

Отдельный точный canary в разрешённом owner-диалоге: новый draft в том же
личном маршруте, `approve_send` с `confirm=true`, readback состояния и
удаление canary-черновика штатным `draft.delete`. Ожидаемый и проверенный
исход — `account_relogin_required`, draft возвращён в `proposed`, у Telegram
ничего не ушло. Реальная доставка невозможна, пока владелец не войдёт заново.

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