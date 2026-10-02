# ChatGPT CDP adapter

Started at 2026-08-31T00:56:56+03:00 (system `date`; host uptime since 2026-08-07T09:13:42+03:00)

## Минимальный путь

- Результат: UserIO видит чаты авторизованной страницы ChatGPT через `megamen32/chatgpt-cdp-mcp` как канал `chatgpt`.
- Canary: настроенный локальный stdio MCP возвращает recent chats и export одного чата через `userio.channels.list/read`.
- Вертикальный срез: read-only stdio bridge с явной переменной команды, `chatgpt` в реестре каналов и контрактными тестами без браузерного профиля.
- Не делаем: CDP-драйвер, хранение браузерных сессий/секретов, автоматическую отправку, деплой или изменения ChatGPT-аккаунта.

Estimate: minimum 15 active minutes; maximum 35 active minutes.
Status: complete. Both project suites pass; a cross-repository mock-driver canary proved `send_draft → approve → chatgpt-cdp-mcp.send_message`. No real ChatGPT browser profile is configured in this checkout.
