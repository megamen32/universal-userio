import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../content/chatgpt-passive-bridge.js', import.meta.url), 'utf8');

function fixture(runtime) {
  let listener;
  let removed = 0;
  const window = {
    addEventListener: (name, fn) => { assert.equal(name, 'message'); listener = fn; },
    removeEventListener: (name, fn) => {
      assert.equal(name, 'message');
      assert.equal(fn, listener);
      removed++;
      listener = undefined;
    }
  };
  const chrome = {runtime};
  const ctx = vm.createContext({window, chrome, crypto: {randomUUID: () => 'page-fixture'}});
  vm.runInContext(source, ctx);
  const emit = () => listener?.({
    source: window, origin: 'https://chatgpt.com',
    data: {type: 'userio-chatgpt-observed-v1', observation: {kind: 'identity', accountId: 'fixture'}}
  });
  return {chrome, emit, removed: () => removed};
}

test('a healthy extension sends observations normally', async () => {
  const sent = [];
  const f = fixture({id: 'fixture-extension', sendMessage: msg => {sent.push(msg); return Promise.resolve();}});
  f.emit();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].kind, 'userioChatGPTObserved');
  assert.equal(f.removed(), 0);
});

test('missing runtime after extension update detaches the obsolete listener', () => {
  const f = fixture({id: 'fixture-extension', sendMessage: () => Promise.resolve()});
  delete f.chrome.runtime;
  assert.doesNotThrow(() => f.emit());
  assert.doesNotThrow(() => f.emit());
  assert.equal(f.removed(), 1);
});

test('synchronous context invalidation is handled and stops further observations', () => {
  let sent = 0;
  const f = fixture({id: 'fixture-extension', sendMessage: () => {
    sent++;
    throw new Error('Extension context invalidated.');
  }});
  assert.doesNotThrow(() => f.emit());
  f.emit();
  assert.equal(sent, 1);
  assert.equal(f.removed(), 1);
});

test('transient asynchronous message rejection does not detach the listener', async () => {
  let sent = 0;
  const f = fixture({id: 'fixture-extension', sendMessage: () => {
    sent++;
    return Promise.reject(new Error('Could not establish connection.'));
  }});
  f.emit();
  await new Promise(r => setImmediate(r));
  f.emit();
  await new Promise(r => setImmediate(r));
  assert.equal(sent, 2);
  assert.equal(f.removed(), 0);
});
