const SERVICE_ALIASES = {
  email: 'imap',
  phone: 'calls',
  vk: 'vk-personal',
}

const CONNECTABLE_SERVICES = new Set([
  'matrix', 'imap', 'gmail', 'yandex', 'vk-personal', 'vk-community',
  'whatsapp', 'max', 'sms', 'calls',
])

export function accountConnectHref(platform) {
  const selected = String(platform || '').trim().toLowerCase()
  if (!selected || selected === 'all') return '/accounts/connect/new'
  if (selected === 'telegram') return '/telegram-qr/'
  if (selected === 'chatgpt') return '/connect-mcp'
  const service = SERVICE_ALIASES[selected] || selected
  if (!CONNECTABLE_SERVICES.has(service)) return '/accounts/connect/new'
  return `/accounts/connect/new?service=${encodeURIComponent(service)}`
}
