# Личное чтение Telegram восстановлено после остановки зависимого ingress

В рамках разрешённого владельцем восстановления UserIO для автосводок проверен
новый отказ `telegram-qr connector is unreachable`. Это другой сбой после
принятого HTTP-CONNECT880: личный ingress18095 был остановлен systemd14:52:34Z
при stop/start `universal-userio.service`. API снова работал, но его Requires-
dependent автоматически не запустился. Voice owner01a1369d подтвердил причину
и владеет исправлением своего deploy lifecycle для всех прежних active dependents.

Один `systemctl start userio-telegram-ingress.service`18:18:14МСК восстановил
существующий клиент/PID722096. Код, конфигурация, сессии, ключи и конечные
MemoryHigh224MiB/Max256MiB/swap64MiB/CPU1/tasks64 сохранены по точным fingerprint.
Backend PID440793 не менялся; engine, relay, gateway, calls не перезапускались.
Поддерживаемый channels.read личного540/Dmitry conv_2bb6… снова refreshed/freshtrue,
34 actual messages, has_morefalse; readACK и отправок не было. Native Менеджер
затем успешно прочитал все9 выбранных рабочих источников своим обычным SDK job.

Отдельная неизвестная отправка Lexis owner сохраняется: точный draft
`draft_96588d501f8d4eae9115db111ab57ca4`, conversation conv_c7b6300e97fa96bb0e4d789e,
account540→peer881, sending/outbox_receiptNULL. Она не повторена и не освобождена
прямой записью БД. Отрицательная lifecycle/provider проверка сохранена у sender6d;
публичного product reconciliation этого claim нет. Это явная незакрытая граница
UserIO draft lifecycle, не доказательство провала текущего чтения или Manager прав.
Минимальный отдельный repair: product operation с неизменным draft snapshot и
проверяемым before-request negative outcome, затем штатный disposition без send.
Sender6d принял guarded сохранение и проверил права новой отдельной обычной UAT.

Доказательства приватны в Exmanager
var/20261014-manager-autonomy-live/connector-recovery/{restore-intent,restore-result,
fresh-read,unknown-draft-read-*}.json; root helper20261014-manager-personal-ingress-recovery.py.
Диагностика read-only; recovery sends0/replay0/sessioncreation0. Чужие WIP UserIO,
Matrix/Telegram calls, Yandex/Telemost и body extractor сохранены.
