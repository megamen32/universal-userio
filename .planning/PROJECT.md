# Universal UserIO

## What This Is
Единый личный inbox с Telegram, MAX, почтой, SMS и звонками, общей историей, кэшем и разбором Fast Agent. Написанный человеком текст отправляется сразу; предложения ИИ — после явного подтверждения. Личные подключения доступны через веб-сайт и MCP (Bearer или OAuth).

## Core Value
Личный аккаунт доступен агентам через UserIO, а переписка соответствует реальному провайдеру.

## Current Milestone: v1.3 Messenger capabilities for the Airlock Secretary

**Goal:** The actual Secretary uses Telegram and MAX through UserIO capabilities
and the native Airlock SDK, preserving its business task and current sender identity.

**Target features:** Effective account/credential capabilities, source-bound
paged history/media, immutable approved replies and rich operations, real native
consumer acceptance and scoped retirement of replaceable Telegram transport.

User directly authorized execution on 2026-10-12. Prior v1.2 acceptance and all
historical receipts/plans are preserved under `.planning/milestones/`.

## Requirements
### Validated
- Telegram 900000300: реальные сообщения, кэш и Fast Agent подтверждены 2026-10-11.
- Общий механизм черновика/подтверждения/receipt существует.
- MAX: постоянная серверная сессия, реальная история, live sync, человеческая веб-отправка, повторные URL/plain edits на том же ID и стикеры.
- Звонки: диалог по человеку, видимые номера, начало/конец и история; неизвестные старые номера оставлены в архиве по решению пользователя.
- SMS: один нативный аккаунт, полные сообщения и постоянная синхронизация; APK/инструкции для чужих rooted/unrooted Android.
- Компактный интерфейс, авторы групп, landing/cabinet/GitHub, read/stealth, короткий README и 27 разделов документации.
- Несколько персональных Bearer-токенов, независимый revoke, существующий GPTAdmin; реальный UserIO OAuth-плагин ChatGPT на Mac M1 прочитал 13 аккаунтов.
### Delivered in v1.2
- [x] Секретарь различает автора/собеседника и показывает точный исходник, время и актуальность черновика.
- [x] Устранить подтверждённую задержку/401 dispatcher, включить реальные рабочие чаты через существующую политику.
- [x] Уведомления почты объясняют содержание и требуемое действие.
- [x] Итоговый original GSD acceptance gate с реальным consumer proof всех оставшихся требований.
### Out of Scope
- Подмена личного аккаунта ботом: не читает личную переписку.
- Постоянная автоматизация Android/браузера: пользователь отверг как основной транспорт.
- Массовые отправки, повтор исторических принятых действий и отменённое расширение delete-six Exmanager.

## Context
GSD уже инициализирован в этом продукте, native recognition complete. Исправляется только дрейф формата/progress и покрытие последующих поручений; исходная история сохранена в milestones. Родительский Agent Projects Portfolio не изменяется. Source, runtime и реальные consumer receipts различаются; последнее нельзя заменять тестом или открытой страницей.

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

Last updated: 2026-10-12 after actual secretary/work/mail acceptance and maintained Matrix catch-up. Source/runtime/provider/user-surface boundaries remain explicit in phase summaries.
