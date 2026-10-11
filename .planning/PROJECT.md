# Universal UserIO

## What This Is
Единый личный inbox с каналами, историей, кэшем, разбором Fast Agent и отправкой через черновик и явное подтверждение. Существующие Telegram/SMS и их данные сохраняются.

## Core Value
Личный аккаунт доступен агентам через UserIO, а переписка соответствует реальному провайдеру.

## Current Milestone: v1.0 MAX personal account
Подключить существующий MAX на Android к постоянному серверному клиенту: история, новые события, корректный аккаунт/чат, явная отправка и reconnect без постоянно занятого телефона или браузера. Пользователь разрешил автономный выбор маршрута, внедрение и живую проверку 2026-10-11.

## Requirements
### Validated
- Telegram 900000300: реальные сообщения, кэш и Fast Agent подтверждены 2026-10-11.
- Общий механизм черновика/подтверждения/receipt существует.
### Active
- [x] MAX авторизация и сохраняемая серверная сессия.
- [x] MAX история и live события совпадают с приложением.
- [x] Черновик, явное подтверждение, одна реальная отправка и receipt.
- [x] Reconnect, dedup, ограниченная служба и операторский rollback.
### Out of Scope
- Подмена личного аккаунта ботом: не читает личную переписку.
- Постоянная автоматизация Android/браузера: пользователь отверг как основной транспорт.
- Звонки и массовые отправки: не нужны для inbox.

## Context
MAX adapter уже существует, но серверная авторизация установлена, unit работает; пять чатов/35 сообщений и одна подтверждённая отправка проверены. Локальный GSD создаётся именно в этом продукте; родительский Agent Projects Portfolio не изменяется.

## Constraints
- Main, канонический checkout, чужой WIP сохраняется.
- S21 только через lease; сессии и секреты вне Git.
- Перед отправкой все изменения, проверки и install завершены.

## Key Decisions
| Decision | Rationale | Outcome |
|---|---|---|
| Server WebSocket personal session | Existing adapter and personal API projects; official HTTP API is bot-only | Accepted: server identity and live exchange |
| Sequential inline execution | One owner; no duplicate client/enrollment | Active |

## Evolution
После фазы обновлять требования по реальному consumer proof; после milestone пересматривать статус и решения.

Last updated: 2026-10-11 after MAX milestone initialization.
