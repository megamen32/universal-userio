# UI fix: топ-5 фиксов из ревью Марата

Started at 2026-09-04T04:53:00+03:00 (manual clock, по .tmp/ui-server.log и `/proc/uptime`).
Cycle estimate: minimum 45 / maximum 90 active minutes.
Branch: main, dirty: src/universal_userio/{adapters.py,runtime.py} (telegram-qr outbox, не моя зона).

Source: .tmp/reviews/reviews-marat-2026-09-04.md — 16 проблем, severity 1–5.

## Минимальный путь (3 строки)

1. **Результат:** в `web/universal-userio-web/src/App.tsx` продажник видит чаты упорядоченные по свежести, поиск находит сообщения по всему телу письма/истории, черновик изолирован строго по чату, аккаунты показывают health-индикатор; backend отдаёт `/v1/conversations/search` с морфологией и поиском по полной истории.
2. **Короткий реальный canary:** на изолированном dev-стенде 127.0.0.1:18093 логинюсь как `uitest`, открываю список — `+79103332444` (03.09) выше `billing@supplier.ru` (30.08), ищу `догов` — находятся оба (`+79103332444` и `wa:2337…5482`), набираю draft в одном чате, переключаюсь на другой — поле пустое, аккаунты в сайдбаре показывают зелёный кружок онлайн.
3. **YAGNI срез:** 3 правки в `App.tsx` (сортировка, очистка draft через useEffect, health-индикатор из `capabilities`) + 1 endpoint `/v1/conversations/search?q=` + фронт-вызов с дебаунсом 200мс. Без новых зависимостей, без редизайна, без смены тем.

## Не делаем сейчас (discard)

- Настоящий морфологический движок (pymorphy2/elasticsearch) — используем подстрочное `icontains` + простой стемминг суффиксов русского (`-ите`, `-йте`, `-ый`,`-ая`,`-ое`).
- Indexed search (FTS5 в SQLite) — отдельный цикл.
- Новые темы/иконки/редизайн меню.
- Native mobile UX / PWA / push notifications.
- Health-check через polling (используем уже имеющееся поле `capabilities` + новый `last_synced_at`).
- Миграция других каналов — только то, что нужно для 4 проблем.
- Тесты Playwright/E2E — сначала unit + ручной canary, автотесты добавим следующим циклом.

## Что чиним (приоритет)

1. **#1 Сортировка по свежести** — добавить `.sort((a,b) => (b.last_at ?? 0) - (a.last_at ?? 0))` к `visibleChats`. Severity 5.
2. **#4 Изоляция черновика** — добавить `useEffect(() => setDraft(""), [conversation?.id])` ИЛИ `useEffect(() => setDraft(""), [selectedChat])`. Также при размонтировании диалога очищать. Severity 5.
3. **#2 Поиск по морфологии + контенту** — endpoint `/v1/conversations/search?q=` (бэк) + клиентский стемминг (фронт). Severity 5.
4. **#6 Health-индикатор аккаунтов** — добавить кружок `bg-emerald-500` если `last_synced_at` < 5 мин назад, `bg-amber-500` 5-60 мин, `bg-rose-500` > 60 мин/error. Severity 4.

## Результат / Definition of Done

- `git diff` показывает только изменённые `App.tsx` и `http_api.py` (или новый модуль поиска).
- Ручной canary на 18093 проходит все 4 пункта.
- Существующие тесты проходят: `pytest tests/ -q`.
- Vite build: `npm run build` в `web/universal-userio-web/`.
- 1 маленький коммит с ясным message.

## Что наблюдать

- Бэкенд `/v1/conversations` уже сортирует? — проверить `store.py` и `service.py`.
- Поле `last_synced_at` у `Account` — есть ли? Если нет, добавить в `accounts` стора.
- Стабильность `useEffect` — не сбросить draft во время ввода (зависимость только от `selectedChat`, не от `conversation`).
