# Gmail Himalaya reply

Started at 2026-09-01T20:15:53+03:00 (manual clock). Cycle estimate: minimum 15, maximum 35 active minutes.

Результат: approve Gmail-draft отправляет письмо через уже настроенный Himalaya SMTP и не ломает остальные каналы.
Канарейка: тестовый Gmail draft вызывает Himalaya с account, получателем и body; на проде read-only флаг снят только после безопасной SMTP-проверки, а реальная отправка остаётся на explicit approve.
Срез: добавить Himalaya outbox для `gmail:<alias>`, включить reply capability у существующих Gmail account, покрыть команду тестом и отправить тестовое письмо в собственный mailbox только при необходимости.
Не делаем: OAuth, новый secret-store, почтовый UI, массовую отправку, переработку Inbox.

Status: complete. Approved Gmail drafts now call the configured Himalaya SMTP account with From/To and In-Reply-To headers. Existing Gmail accounts have reply capability enabled. Live canary approved `draft_112508cd902c45a1a700c14b2f659754` with receipt `himalaya:careviolan:draft_112508cd902c45a1a700c14b2f659754`; service active. The server's router DNS was non-responsive; a reversible active-link override to 1.1.1.1/8.8.8.8 was required for Gmail resolution.
