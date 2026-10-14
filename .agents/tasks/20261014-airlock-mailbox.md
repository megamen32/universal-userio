# Служебная почта Airlock

Прямое поручение пользователя через root: создать на существующем почтовом
хостинге ящик airlock@bezrabotnyi.com, подключить IMAP/SMTP к UserIO владельца,
до включения ingestion исключить ВЕСЬ новый аккаунт из AIсекретаря, сохранить
явное чтение OTP и ручную разрешённую отправку. Не использовать личную почту.
Последнее уточнение адреса заменяет первоначальное airlock-demo@.

Исполнитель userio_connector_repair. Владение: минимальное account-scoped
исключение в store/service, focused тест, почтовые credentials/config только
нового аккаунта, mail runbook/inventory в ServersAdministartion. Чужие
messenger/Telegram/voice изменения и процессы сохраняются; новых checkout нет.
План UserIO .planning/STATE.md/фаза17 не заменяется этим операционным поручением.

Исходный хостинг найден: DMS+Roundcube на44, MX mail.bezrabotnyi.com.
Ящик airlock@ свободен. UserIO имел только chat exclusions, не account-wide
защиту будущих писем. Сделан минимальный opt-out через существующие per-user
preferences; raw inbox и explicit manual-send не изменяются.

Прежде создания доступа: 0600 локальные две копии и немедленный приватный
облачный upload+readback. Найденный signup helper использует Яндекс.Диск,
а прямой Яндекс ObjectStorage пока не найден; это сообщено root без ложного S3green.
Новые пароли не выпускаются до разрешения точного backup dependency.

Принятие: реальная SMTPsubmission с новогоящика самому себе, IMAPполучение,
реальный UserIO read, account policy false, eligible/AIjobs/AI drafts0.
Source тесты не заменяют это принятие.

## Принято 14 октября, 21:13 UTC

Прямое уточнение пользователя: «Яндекс диск»; ожидание ObjectStorage отменено.
До создания ящика private0600 запись +отдельнаякопия100 +yadisk upload/readback
PASS. Пароль не менялся/не публиковался; после добавления account metadata те
же копии снова проверены. Credentialref `himalaya:airlock`, account `gmail-airlock`.
Полный private доступ `~/.config/airlock-marketplace-accounts/mail-airlock.json`.

AI opt-out установлен ДО первого ingestion для accountID и alias. Новая почта
реально прочитана через плагин UserIO; allowedfalse, eligible0, triage0,
AIgenerateddrafts0, summaries0. Единственный ручной testdraft отправлен штатным
плагином самому этому ящику, SMTPreceipt и реальный IMAPUID2 подтверждены.
Первое прямое SMTPselfписьмо дало250queue1F46EB40080 и IMAPUID1.
Всего два контролируемых selfписьма: транспорт и настоящий UserIO outbox;
посторонних получателей нет, принятые отправки не повторялись.

На реальном Dovecot письме найдена folding-whitespace ошибка Message-ID;
исправлен только HimalayaReader ID normalization и совместимость старогоcursor.
Старые сохранённые сообщения не переписывались и не переигрывались.
42 account/workspace/Secretary unitPASS27.29с,21 Gmail/account unitPASS1.45с.
Первый projectvenv запуск не состоялся (pytest отсутствовал), systemPython
штатно выполнил focused suite; обычное warning существующего pytestconfig сохранено.

Published source78beeed/5dbf057; normal rollback-first install. Проверка182
runtimefiles показала доinstall только собственные store/service отличия.
Два коротких APIrestart для двух последовательных sourcefix, каждый вернул
все7 прежде активных RequiredBy ingress. Никаких livecalls не было; старый
Matrixledgerringing былAPIunknown и не повторялся. Budgets не менялись.
Parent/signupowner получил actualaddress/accountID/ref/proof через единый
`airlock-admin/.agents/shared-session/marketplace-chat.jsonl`, без секретов.

Canonical mail runbook: ServersAdministartion/docs/runbooks/domain-mail.md.
Подробная проверка: docs/verification/20261014-airlock-mailbox.md.
