# Airlock mailbox: реальный UserIO consumer

14 октября 2026, 21:13 UTC. Создан только airlock@bezrabotnyi.com на существующем
DMS44. Canonical UserIO account `gmail-airlock`, alias `airlock`, secretref
`himalaya:airlock`. Основной private JSON с паролем0600 вне Git; две локальные
копии100 +приватный Яндекс.Диск проверены чтением/сверкой до создания доступа
и после добавления metadata. Прямое уточнение пользователя отменило S3.

## Проверки

| Проверка | Результат |
| --- | --- |
| Public IMAP993 / SMTP587 |TLS validation, AUTH/login PASS |
| Первое selfписьмо |SMTP250, queue1F46EB40080, IMAPUID1,1747bytes; DKIMsignature есть |
| Плагин accounts_list |gmail-airlock enabled, read/reply, sender Airlock |
| Плагин channels_read |настоящий INBOXbody PASS, без ACK/AI |
| Native manual draft/approve_send |draft_b36330dcd961404fbc461508d65d1542, senttrue, receipt himalaya:airlock:draft_b36330dcd961404fbc461508d65d1542 |
| Независимый IMAP нового UserIOписьма |UID2,1998bytes, правильные From/To/body, SHA886594055b2f2b9885e7d7c80451f028a540abc20a24d7ea893ab32ed38e38ae |
| Account-scoped messenger.history |два настоящих selfписьма; raw чтение доступно |
| Account AI policy |account_ai_disabledtrue, allowedfalse для gmail-airlock/airlock |
| Фактические автоматические результаты |eligibleevents0, triage0, AIgenerateddrafts0, summaries0; один явно ручной approveddraft |

Отправлено два разных контролируемых selfписьма, внешних получателей0. Второе
нужно для проверки настоящего UserIO outbox поверх базового SMTPtransport.
Каждый принятый send был выполнен ровно один раз. Исторические отправки,
звонки, пароли других аккаунтов и личная megamen932 почта не использовались.

Первый channels_read с history_limit получилINVALID_ARGUMENT (pagination
толькоTelegram); повтор правильного read без этого аргумента прошёл. Первый
IMAPproof assertion на folded Message-ID не прошёл; письмо уже было SMTPaccepted
и было найдено без повторной отправки. Реальный header содержал ведущийFWS.
HimalayaReader теперь нормализует его ДО угловых скобок и распознаёт legacy
cursor, не переписывая/переигрывая старые сообщения.

## Source, runtime и ограничения

Account-wide opt-out хранится в существующих user_preferences. Проверяется
при ingest policy/listeners, triage/deep, summary generation/save, AIpropose.
Обычное чтение и точная manual draft approval остаются доступны. Разрешение
отдельного чата не отменяет account deny; включение не переигрывает историю.

Source78beeed/5dbf057 опубликован main; штатный runtime_release manifest,
rollback archive, install, verify и проверкаsystemd прошли. До первой установки
из182runtimefiles отличались только собственные service/store. Старые
неизвестные звонки не повторялись. При обоих коротких APIrestart возвращены
все7 активных прежде ingress; контейнеры почты не перезапускались.

Fast unit account/workspace/Secretary42PASS27.29с (expected30/max300), Gmail/
account21PASS1.45с (expected3/max30). Первый projectvenv не имелpytest;
systemPython выполнил реальные focused tests. Это отдельная локальная проверка,
а не замена SMTP/IMAP/плагина. Новых daemon/browsers/modelcalls нет, бюджеты
не повышались. Финальный runtimeverify/Systemd и metadata read дополняют
описанный реальный consumer proof.

Существующий careviolan ingress имел Himalaya pollerror до задачи; этот чужой
аккаунт/ключ не менялся. Его персональная авторизация требует отдельной owning
проверки; это не отказ нового airlock ящика. В почтовом hosting старый
mailserver.service Resulttimeout/ExecStatus130 при работающих Docker SMTP/IMAP:
история не очищалась и контейнеры чужих ящиков ради маркера не перезапускались.
Это не мешало обоим реальным selfcanary.

MX и DKIM подтверждены; SPF/DMARC отсутствуют. Глобальные sender policies
не менялись без сверки всех существующих отправителей. Внешний OTP партнёра
должен быть подтверждён его настоящей регистрацией; selfписьма такого обещания
не дают. Canonical infrastructure runbook находится в
ServersAdministartion/docs/runbooks/domain-mail.md.
