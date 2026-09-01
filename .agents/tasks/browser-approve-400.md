# Browser approve 400

Started at 2026-09-01T20:00:29+03:00 (manual clock). Cycle estimate: minimum 12, maximum 25 active minutes.

Результат: кнопка Approve/Send в веб-панели успешно подтверждает подготовленный draft.
Канарейка: воспроизвести `POST /v1/drafts/draft_112508cd902c45a1a700c14b2f659754/approve` с тем же authenticated browser user и получить успешный ответ без непреднамеренной внешней доставки.
Срез: выявить конкретное условие 400, добавить узкий регрессионный тест, исправить обработчик/данные и проверить API; затем, если безопасно, проверить UI.
Не делаем: favicon, редизайн UI, новые каналы доставки, расширение схемы авторизации, массовый resend.

Status: implementation verified locally: focused 17/17, full 41/41, and dashboard production build pass. Pending deployment and live read-only UI/API canary; no external message will be sent.
