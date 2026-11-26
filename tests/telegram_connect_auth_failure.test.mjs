import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { authRetryDelaySeconds, classifyAuthError, probeAuthorization } from '../deploy/telegram-qr-connector/session-auth.mjs';

async function runFailedConnection(error) {
  const source = readFileSync(new URL('../deploy/telegram-qr-connector/server.mjs', import.meta.url), 'utf8');
  const body = source.slice(source.indexOf('async function syncAccount(slot)'), source.indexOf('// --- operator surface'));
  const slots = new Map();
  const disconnectedSlots = new Set();
  let delay;
  const context = {
    syncs: slots, disconnectedSlots, sessionsDir: '/unused', liveSlots: new Map(),
    readFileSync: () => 'fixture',
    newSyncClient: () => ({ connect: async () => { throw error; }, disconnect: async () => {} }),
    setSync: (slot, patch) => slots.set(slot, { ...(slots.get(slot) || {}), ...patch }),
    setTimeout: (callback, ms) => { delay = ms; disconnectedSlots.add('account-5'); callback(); },
    syncRetrySeconds: 30, authRetryDelaySeconds, classifyAuthError, probeAuthorization,
    console: { error() {}, log() {} },
  };
  const syncAccount = vm.runInNewContext(`(${body.trim()})`, context);
  await syncAccount('account-5');
  return { state: slots.get('account-5'), delay };
}

test('connect refusal for an invalidated key requires login and avoids a short retry loop', async () => {
  const { state, delay } = await runFailedConnection({ errorMessage: 'AUTH_KEY_DUPLICATED', message: '406: AUTH_KEY_DUPLICATED (caused by InvokeWithLayer)' });
  assert.equal(state.status, 'relogin-required');
  assert.equal(state.authCode, 'revoked_auth_key');
  assert.equal(delay, 900000);
});

test('connection failures remain retryable', async () => {
  const { state, delay } = await runFailedConnection({ errorMessage: 'TIMEOUT', message: 'timeout' });
  assert.equal(state.status, 'retrying');
  assert.equal(delay, 30000);
});
