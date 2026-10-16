# Telegram: пароль, код и телефон действительно доходят до сервера

Поручение владельца17Oct: исправить UserIO после «ввёл пароль, дальше не
пускает». Root X-manager делегировал доведение точечной правки до main,
установки и реального HTTP proof; сам worker пароль не получает.
Parent: Exmanager `.agents/tasks/20261017-meeting-delivery-and-work-task-repair.md`.

Browser async формы отправляли multipart FormData, сервер читал urlencoded.
Выполняемый server.mjs содержал этот же дефект; password/code/phone терялись.
Исправлена только сериализация submit: URLSearchParams(FormData(form)).
Авторизация владельца и текущая provider login session сохраняются.

Ownership: одна строка `deploy/telegram-qr-connector/server.mjs`, новый
`tests/telegram_login_form.test.mjs`, этот отчёт и точечный installer.
Чужие reconcile hunks server.mjs, messenger.py и остальные WIP не публикуются.
Before diff/SHA сохранены0600 в `var/20261017-login-form-repair`;
обратное снятие нашего hunk побайтово совпало с before SHA.

Fast unit исполняет настоящие submit-handler и readBody с dummy полями:
RED3FAIL0.222с → GREEN3PASS0.225с; expected2/max20с, Node heap128MiB,
один процесс в унаследованном guard. Ни Telegram API, ни пароль не участвуют.
AgentMemory startup сейчас transport unavailable; выводы по live/source.
Original GSD: complete, без повторной инициализации; ограниченный bugfix
не закрывает остальные незавершённые фазы v1.3.

Установка: новый тонкий образ поверх принятого92f8ffc8c7cf, только опубликованный
Git object server.mjs; один build1GiB/CPU2/PIDs128/networknone, expected30/max300с,
не менее20GiB host reserve. Backend сохраняет3GiB+256MiBswap,22binds и остальные
живые параметры; frontend/voice не меняются. Перед остановкой — account4
сохранён0600, sync backfill завершён и нет активных встреч/звонков.
При неудаче возвращается прежний контейнер/config, новые receipts/БД не откатываются.

Приёмка: опубликованная строка установлена, actual HTTP-served submit
отправляет dummy fields корректно в ограниченный локальный HTTP receiver;
никакого нового Telegram login/passwordPOST. После рестарта account540 остаётся
на account4 и настоящие fresh bounded personal-chat probes успешны.
До этих проверок source GREEN не называется исправленным live UI.

Первая установка e2ec951 честно откатилась: installer требовал завершить
весь первоначальный100-chat backfill за120с, хотя счётчик рос65/100,
ошибок не было и обе private Telegram reads уже были fresh=true.
Production временно вернулся к принятому92 без отката БД/session.
Исправлен только installer: authenticated connected+saved account с живым
backfill принимается как готовый транспорт (liveSlots создаётся ДО scan),
наблюдение за auth имеет конечный emergency budget480с; отдельные real fresh
reads обязательны после установки. Повторная активация имеет отдельный
release-v2 intent, а не повтор failed intent. Новый login не создаётся.
