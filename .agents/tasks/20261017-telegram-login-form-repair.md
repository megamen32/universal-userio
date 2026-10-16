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

Доставлено: e2ec951 source form/test; dc5e625 исправляет только installer
readiness. Оба в origin/main. Живой backend
`universal-userio-login-form:dc5e625bbeae`, container4f298ff8e778,
published server SHA409498aea9c30d85e4dc7e3ace4966c0064f9d48d643487b9ef9d5170b43ea7c.
Вторая активация56.469с, все прежние конфигурации/22binds/лимиты совпали.
Rollback: userio-backend-prev-login-form-v2-20261017, образ92f8ffc8c7cf.

Реальная HTTP приёмка: страница из работающего backend вернула200;
её настоящий inline submit handler выполнил3HTTPPOST с dummy полями
в ограниченный loopback receiver (VM harness, не headed browser).
До установки3/3 поля терялись, после3/3 совпали, content-type=urlencoded.
Provider passwordPOST/new login=0. Private0600: http-before.json,
http-after-v2.json, release-v2/{intent,prepared,receipt}.json в
var/20261017-login-form-repair. Первый failed install сохранён отдельно
release/failure.json; неизвестные provider действия не повторялись.

После окончательного рестарта actual native private probes Григория/Олега
и Forenergo MAX fresh=true; read acknowledged=false. Exact Telegram540
enabled/read+reply, credential_ref=telegram-qr:account-4. MAX дополнительно
прошёл тот же refresh limit40, который использует work collect:40сообщений,
ошибок нет. Причина предыдущего единичного catchup MAX refusal не установлена;
bounded safe logs не дали exception/provider класса, она не выдумывается.
Никакого второго catchup job worker не запускал.

Frontend5723e9266501/history-8e76b945dfdd работает с21:41:19UTC,
voice027f81f60562/telemost-live с13:03:56UTC; оба не перезапускались.
Короткая пользовательская документация: docs/telegram-sign-in.md.
