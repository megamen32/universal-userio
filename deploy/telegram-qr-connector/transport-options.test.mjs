import assert from 'node:assert/strict';
import test from 'node:test';
import { createRequire } from 'node:module';
import { telegramTransportOptions } from './transport-options.mjs';
const require = createRequire('/opt/userio-telegram-qr/package.json');
// Load the public entry point before connection modules (GramJS CJS cycle).
const { TelegramClient } = require('telegram');
const { StringSession } = require('telegram/sessions/index.js');
const { ConnectionTCPObfuscated } = require('telegram/network/connection/TCPObfuscated.js');
const { PromisedNetSockets } = require('telegram/extensions/PromisedNetSockets.js');
import { httpConnectOptions } from './http-connect-socket.mjs';

test('selected obfuscated HTTP transport uses 443 on the installed GramJS client', async () => {
  const options = {
    ...telegramTransportOptions('obfuscated', ConnectionTCPObfuscated),
    ...httpConnectOptions('http://127.0.0.1:3127', PromisedNetSockets),
  };
  const client = new TelegramClient(new StringSession(''), 1, 'test-only', options);
  let captured;
  // Exercise the actual connect constructor path without opening a socket,
  // using credentials, creating an auth key or starting a provider session.
  client._sender = { connect: async connection => { captured = connection; return true; }, send: async () => ({}), isConnected: () => true };
  client._loopStarted = true;
  await client.connect();
  assert.ok(captured instanceof ConnectionTCPObfuscated);
  assert.equal(captured._port, 443);
  assert.equal(captured._proxy.port, 3127);
  assert.equal(client.useWSS, false);
});

test('default full transport is preserved and invalid selection fails closed', () => {
  assert.deepEqual(telegramTransportOptions(undefined, ConnectionTCPObfuscated), {});
  assert.deepEqual(telegramTransportOptions('full', ConnectionTCPObfuscated), {});
  assert.throws(() => telegramTransportOptions('typo', ConnectionTCPObfuscated), /Unsupported TELEGRAM_TRANSPORT/);
});
