# Epic: real attachment download per channel

Started at 2026-09-04T05:25:00+03:00 (manual clock).
Cycle estimate: minimum 60 / maximum 120 active minutes.
Branch: main, dirty: src/universal_userio/{adapters.py,runtime.py} (telegram-qr outbox, не моё).

Source: review Marat 2026-09-04, fix #3 follow-up (commit fdc6d6f уже отрисовал
UI модал с «available=false», но это заглушка: ни один
`StoredChannelAdapter.download` не реализован — все кидают `AdapterNotSupported`).

## Минимальный путь (3 строки)

1. **Результат:** оператор на dev-стенде `127.0.0.1:18093` логинится, открывает чат с email-вложением (PDF/картинка), кликает на плашку `[Документ]` → модал показывает реальный превью + кнопку «Скачать», которая возвращает байты файла из IMAP.
2. **Канарейка:** на изолированном IMAP через Python `imaplib` создать fixture-письмо с PDF-вложением; ingest в UserIO; ручной canary через `/v1/conversations/{id}/media/{message_id}` возвращает `Content-Type: application/pdf` и реальные байты; фронт-превью показывает первую страницу PDF.
3. **YAGNI срез:** только `MailChannelAdapter.download` через `EmailChannel.from_env()`; остальные 4 адаптера оставляем `AdapterNotSupported` с явной причиной; backend endpoint дополняется `download_url` в meta-ответе; frontend кнопка «Скачать» получает href.

## Не делаем сейчас (discard)

- Telegram/WhatsApp/VK/SMS adapters — следующие циклы (нужны сессии/credentials).
- Multi-account IMAP routing (по alias `gmail:careviolan` vs `gmail:megamen932`).
- Превью Office-документов (.docx/.xlsx) — только image и PDF.
- Кеширование скачанного на диск — stream сразу в HTTP-ответ.
- Авторизация per-file — берётся тот же bearer token что и на остальной API.
- Шифрование at rest, аудит-лог скачиваний.
- Бандл-download / ZIP нескольких файлов.
- Видео/аудио превью.
- Drag-and-drop preview в модале.

## Что реализуем в этом цикле

1. `MailChannelAdapter.download(file_ref)` — резолвит `chat_id` через
   `service._store.message()`, достаёт `EmailChannel.from_env()`, вызывает
   `download_media(chat=peer, message=uid)`. Маппит `DownloadedMedia` → `ChannelFile`.
2. Расширение `/v1/conversations/{id}/media/{message_id}` — когда `available=true`,
   возвращает `download_url` (`/v1/conversations/{id}/media/{message_id}/raw`),
   `filename`, `content_type`, `size`. Метод отдаёт поток байт (image/pdf/прочее).
3. Frontend — модал получает `download_url`, превью `<img>` для `image/*`,
   `<iframe src=...#toolbar=0>` для PDF, иначе показывает имя + размер и кнопку
   «Скачать» с `<a href download>`.

## Что наблюдать / проверить

- IMAP host/port/user/password должны быть в env (`USERIO_SMTP_*` / `USERIO_IMAP_*`).
  На dev-стенде 18098 — есть ли они? Без них canary не пройдёт.
- Размер вложения: HTML iframe с PDF может не работать с `sandbox=""` если PDF
  большой — проверить.
- `attachment_url` в `Message` — если он уже есть, использовать его напрямую
  вместо download.

## Definition of Done

- `pytest tests/test_http_api.py` и `pytest tests/test_channels_email.py` зелёные.
- Vite build OK.
- Ручной canary на 18093: открыть чат → кликнуть плашку → модал с реальным
  файлом → кнопка «Скачать» возвращает bytes.
- 1 коммит с ясным message.
