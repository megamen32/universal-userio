export async function logoutTelegramClient(client, Api) {
  if (!client || typeof client.invoke !== 'function') {
    throw new Error('live Telegram client is missing')
  }
  const LogOut = Api?.auth?.LogOut
  if (typeof LogOut !== 'function') throw new Error('Telegram auth.logOut is unavailable')
  return client.invoke(new LogOut())
}
