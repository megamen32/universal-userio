# Gmail Himalaya reply

Started at 2026-09-01T20:15:53+03:00 (manual clock). Cycle estimate: minimum 15, maximum 35 active minutes.

Результат: approve Gmail-draft отправляет письмо через уже настроенный Himalaya SMTP и не ломает остальные каналы.
Канарейка: тестовый Gmail draft вызывает Himalaya с account, получателем и body; на проде read-only флаг снят только после безопасной SMTP-проверки, а реальная отправка остаётся на explicit approve.
Срез: добавить Himalaya outbox для `gmail:<alias>`, включить reply capability у существующих Gmail account, покрыть команду тестом и отправить тестовое письмо в собственный mailbox только при необходимости.
Не делаем: OAuth, новый secret-store, почтовый UI, массовую отправку, переработку Inbox.

Status: tracing current Universal Inbox Gmail envelope and Himalaya CLI invocation.
