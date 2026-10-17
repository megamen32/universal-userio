import test from 'node:test';
import assert from 'node:assert/strict';
import { accountConnectHref } from '../web/universal-userio-web/src/account-connect.mjs';

test('Telegram add opens its QR login directly', () => {
  assert.equal(accountConnectHref('telegram'), '/telegram-qr/');
});

test('provider picker keeps the selected provider in its setup URL', () => {
  assert.equal(accountConnectHref('gmail'), '/accounts/connect/new?service=gmail');
  assert.equal(accountConnectHref('matrix'), '/accounts/connect/new?service=matrix');
});

test('generic add and ChatGPT add have their own destinations', () => {
  assert.equal(accountConnectHref('all'), '/accounts/connect/new');
  assert.equal(accountConnectHref('chatgpt'), '/connect-mcp');
});
