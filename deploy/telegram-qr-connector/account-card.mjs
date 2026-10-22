const statusLabels = {
  idle: 'Ожидает начала входа',
  waiting: 'Ожидает сканирования QR-кода',
  connecting: 'Выполняется вход',
  connected: 'Аккаунт подключён',
  'connected-unregistered': 'Вход есть, аккаунт не сохранён',
  'code-required': 'Нужен код Telegram',
  'password-required': 'Нужен облачный пароль Telegram',
  error: 'Вход не завершён',
};

function accountSuffix(accountId) {
  const value = String(accountId || '').replace(/^telegram:/, '');
  return value ? value.slice(-5) : '';
}

function slotNumber(slotId) {
  return /^account-(\d+)$/.exec(String(slotId || ''))?.[1] || '';
}

export function telegramAccountCard(session, account = null) {
  const revoked = session.sync === 'relogin-required';
  const missing = session.status === 'reconnect-required' || session.sync === 'missing-session';
  const suffix = accountSuffix(session.accountId);
  const displayName = String(account?.display_name || session.name || '').trim();
  const title = displayName
    ? `${displayName} · Telegram${suffix ? ` · ${suffix}` : ''}`
    : suffix ? `Telegram · ${suffix}`
      : `Сохранённая сессия Telegram №${slotNumber(session.id) || session.id || 'новая'}`;
  const managed = account?.can_disconnect === true && !!session.accountId;
  const accountFound = !!account;

  return {
    title,
    reconnect: revoked || missing,
    status: missing ? 'Ошибка подключения · переподключите Telegram' : revoked ? 'Сессия отключена · Telegram отозвал вход'
      : statusLabels[session.status] || 'Проверяем состояние входа',
    note: missing
      ? 'Для этого профиля нет сохранённого сеанса. Подключите тот же Telegram-аккаунт заново: его адрес и сохранённые переписки останутся прежними.'
      : revoked
      ? `${displayName ? 'Telegram отозвал эту сессию. Она не подключена; сохранённые переписки останутся в UserIO.' : 'Имя аккаунта не сохранено в UserIO. Telegram уже отозвал вход, поэтому проверить его не удалось. Сессия отключена; сохранённые переписки останутся в UserIO.'}`
      : session.sync === 'retrying'
        ? 'Синхронизация сообщений восстанавливается.'
        : session.sync
          ? `Синхронизация сообщений включена${session.syncChats ? ` · ${session.syncChats} диалогов` : ''}.`
          : '',
    sessionLabel: !missing && session.id ? `Сессия ${session.id}` : '',
    action: managed
      ? { kind: 'account', label: revoked ? 'Убрать из UserIO' : 'Отключить аккаунт', accountId: session.accountId }
      : revoked && !accountFound
        ? { kind: 'revoked-slot', label: 'Убрать из UserIO', slot: session.id }
        : null,
  };
}
