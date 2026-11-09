import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { randomUUID } from 'node:crypto';

const source = fs.readFileSync(new URL('../lib/userio.js', import.meta.url), 'utf8');
function profile(initial = {}) {
  const data = { ...initial };
  const chrome = { storage: { local: {
    async get(defaults) { return { ...defaults, ...data }; },
    async set(patch) { Object.assign(data, patch); },
  } } };
  const self = { USERIO_CONFIG: { agentId: 'vk-browser' } };
  vm.runInNewContext(source, { self, chrome, crypto: { randomUUID }, URL });
  return { data, settings: self.UserIO.settings };
}
test('fresh profiles create distinct persistent automatic agent identities', async () => {
  const a = profile(), b = profile();
  const [first, concurrent, other] = await Promise.all([a.settings(), a.settings(), b.settings()]);
  assert.match(first.agentId, /^browser-[0-9a-f-]{36}$/);
  assert.equal(first.agentId, first.agentInstanceId);
  assert.equal(first.agentId, concurrent.agentId);
  assert.notEqual(first.agentId, other.agentId);
  assert.equal((await a.settings()).agentId, first.agentId);
});
test('existing explicit binding remains stable', async () => {
  assert.equal((await profile({ agentId: 'vk-browser-intel' }).settings()).agentId, 'vk-browser-intel');
});
test('an existing legacy installation keeps its bound agent identity on upgrade', async () => {
  const existing = profile({ agentState: { lastOkAt: 100, ts: 101 } });
  assert.equal((await existing.settings()).agentId, 'vk-browser');
  assert.equal(existing.data.agentId, 'vk-browser');
});
test('two legacy profiles have distinct persistent instance identities', async () => {
  const a = profile({agentId:'vk-browser'}), b = profile({agentId:'vk-browser'});
  const [left,right] = await Promise.all([a.settings(),b.settings()]);
  assert.match(left.agentInstanceId, /^browser-[0-9a-f-]{36}$/);
  assert.notEqual(left.agentInstanceId,right.agentInstanceId);
  assert.equal((await a.settings()).agentInstanceId,left.agentInstanceId);
  assert.equal(left.agentId,'vk-browser');
});
