# ChatGPT CDP adapter

Started at 2026-08-31T00:56:56+03:00 (system `date`; host uptime since 2026-08-07T09:13:42+03:00)

## Минимальный путь

- Результат: UserIO видит чаты авторизованной страницы ChatGPT через `megamen32/chatgpt-cdp-mcp` как канал `chatgpt`.
- Canary: настроенный локальный stdio MCP возвращает recent chats и export одного чата через `userio.channels.list/read`.
- Вертикальный срез: read-only stdio bridge с явной переменной команды, `chatgpt` в реестре каналов и контрактными тестами без браузерного профиля.
- Не делаем: CDP-драйвер, хранение браузерных сессий/секретов, автоматическую отправку, деплой или изменения ChatGPT-аккаунта.

Estimate: minimum 15 active minutes; maximum 35 active minutes.
Status: approved delivery is wired through `userio.draft.approve_send` to `chatgpt-cdp-mcp.send_message`; both project suites pass. A real browser canary remains configuration-dependent (`CDP_CHAT_DRIVER_MODULE` is not configured in this checkout).
