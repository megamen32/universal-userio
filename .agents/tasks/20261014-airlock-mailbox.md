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
