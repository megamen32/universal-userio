# Источники и актуальность

## Эта редакция

Руководство сверено 12 октября 2026 с опубликованным UserIO34c6ba3: HTTP/MCP, native Fast Agent, per-user IMAP/Matrix, телефонное подключение и авторы Telegram/MAX. Присланный пакет документации был исходным материалом; его локальные скриншоты не использованы как доказательство работающего сайта.

Новые инструкции и ссылки на API относятся к текущему `main`. Работающий сервер может иметь другую версию: авторизованный `GET /v1/runtime` возвращает commit/manifest и признак проверки. Схемы MCP запрашивайте через `tools/list`.

## Основные контракты

- [HTTP API](https://github.com/megamen32/universal-userio/blob/main/src/universal_userio/http_api.py).
- [MCP schemas](https://github.com/megamen32/universal-userio/blob/main/src/universal_userio/mcp_surface.py).
- [Runtime и адаптеры](https://github.com/megamen32/universal-userio/blob/main/docs/RUNTIME-REFERENCE.md).
- [Стабильный adapter SDK](https://github.com/megamen32/universal-userio/blob/main/docs/adapter-sdk.md).
- [Release и rollback](https://github.com/megamen32/universal-userio/blob/main/deploy/INSTALL.md).
- [Android phone setup](https://github.com/megamen32/universal-userio/blob/main/docs/PHONE-CONNECT.md).

## Внешняя справка

[Яндекс IMAP/SMTP](https://yandex.ru/support/yandex-360/customers/mail/ru/mail-clients/others), [OpenAI custom MCP](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt), [MCP tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools). Возможности и меню сторонних сервисов могут меняться; проверяйте их текущие инструкции.

## Ограничения

Нет самостоятельного восстановления пароля через email/Telegram; IMAP/Matrix подключение не даёт отправку; VK ID не даёт личные сообщения через обычный VK API. APK SMS не импортирует весь архив и исходящие других приложений; native-root и звонки требуют отдельного операторского подключения. MAX/WhatsApp личные коннекторы неофициальные. Полнота старой истории зависит от канала.

Ремонт ролей секретаря, старых черновиков, задержек worker и качества email-уведомлений записан в активной задаче отдельно. Документация не подменяет их реальную приёмку.

## Сопровождение

Правьте Markdown и navigation.json, затем пересобирайте сайт. [Сборщик и проверки](https://github.com/megamen32/universal-userio/blob/main/tools/userio_docs/README.md). Все остальные инструкции доступны в [полном индексе](https://github.com/megamen32/universal-userio/blob/main/docs/README.md).
