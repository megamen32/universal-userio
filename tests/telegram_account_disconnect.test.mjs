import test from 'node:test';
import assert from 'node:assert/strict';
import { logoutTelegramClient } from '../deploy/telegram-qr-connector/account-disconnect.mjs';

test('disconnect logs out exactly the current QR session through auth.logOut', async () => {
  let calls = 0;
  class LogOut { }
  const client = { async invoke(request) { calls += 1; assert.ok(request instanceof LogOut); return { _: 'auth.loggedOut' }; } };
  const result = await logoutTelegramClient(client, { auth: { LogOut } });
  assert.deepEqual(result, { _: 'auth.loggedOut' });
  assert.equal(calls, 1);
});

test('disconnect fails closed when no live Telegram client owns the session', async () => {
  await assert.rejects(() => logoutTelegramClient(null, { auth: { LogOut: class { } } }),
    /live Telegram client is missing/);
});
