import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
class Element {
  constructor() { this.hidden = false; this.disabled = false; this.children = []; this.listeners = {}; this.textContent = ''; this.value = ''; this.checked = false; this.dataset = {}; this.style = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  reset() { this.children = []; }
}
const flush = () => new Promise((resolve) => setImmediate(resolve));

function boot() {
  const html = fs.readFileSync(path.join(root, 'options.html'), 'utf8');
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
  const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
  const connections = [
    { id: 'work', label: 'Рабочий', endpoint: 'https://userio.test', enabled: true, kind: 'userio' },
    { id: 'personal', label: 'Личный', endpoint: 'https://userio.test', enabled: true, kind: 'userio' },
  ];
  const accounts = [
    { accountKey: 'vk:scope-a', provider: 'vk', accountId: 'a', label: 'VK A' },
    { accountKey: 'chatgpt:scope-a', provider: 'chatgpt', accountId: 'a', label: 'ChatGPT A' },
    { accountKey: 'zai:scope-a', provider: 'zai', accountId: 'a', label: 'Z.ai A' },
    { accountKey: 'userio:user-a:work', provider: 'userio', accountId: 'a', userId: 'user-a', label: 'UserIO A', accountScope: 'work' },
    { accountKey: 'userio:user-a:personal', provider: 'userio', accountId: 'a', userId: 'user-a', label: 'UserIO A', accountScope: 'personal' },
    { accountKey: 'userio:user-b:personal', provider: 'userio', accountId: 'b', userId: 'user-b', label: 'UserIO B', accountScope: 'personal' },
  ];
  const bindings = { 'vk:scope-a': ['work', 'personal'], 'chatgpt:scope-a': ['work', 'personal'], 'zai:scope-a': ['work', 'personal'], 'userio:user-a:work': ['work', 'personal'], 'userio:user-a:personal': ['work', 'personal'], 'userio:user-b:personal': ['personal'] };
  const calls = [];
  const chrome = { storage: { local: { get: async (defaults) => defaults, set: async () => {} } }, runtime: { sendMessage: (message, callback) => {
    calls.push(message); let result = { ok: true };
    if (message.kind === 'connectionsList') result = { ok: true, connections };
    if (message.kind === 'accountsList') result = { ok: true, accounts };
    if (message.kind === 'accountBindingsGet') result = { ok: true, bindings };
    if (message.kind === 'accountBindingsSave') bindings[message.accountKey] = message.connectionIds;
    if (message.kind === 'userioStatus') result = { ok: true, connected: true, accountLabel: 'Пользователь' };
    if (message.kind === 'gptConnectionStatus') result = { ok: true, accounts: [] };
    callback(result);
  } } };
  const context = { window: { USERIO_CONFIG: {}, confirm: () => true }, document: { getElementById: (id) => elements[id] || (elements[id] = new Element()), createElement: () => new Element() }, chrome, setTimeout: () => 0 };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'options.js'), 'utf8'), context);
  return { elements, calls, bindings, accounts, connections, html };
}

test('connection UI renders named endpoint cards and independent account destinations', async () => {
  const state = boot(); await flush();
  assert.match(state.html, /VK Web|VK/); assert.match(state.html, /Z\.ai/); assert.match(state.html, /ChatGPT/);
  assert.equal(state.elements.connections.children.length, 2);
  assert.equal(state.elements.connectionAccounts.children.length, 6);
  for (const key of ['vk:scope-a', 'chatgpt:scope-a', 'zai:scope-a', 'userio:user-a:work', 'userio:user-a:personal', 'userio:user-b:personal']) {
    assert.ok(state.elements.connectionAccounts.children.some((row) => row.dataset.accountKey === key), key);
  }
});

test('bindings save by stable accountKey with selected connection ids', async () => {
  const state = boot(); await flush();
  const row = state.elements.connectionAccounts.children.find((item) => item.dataset.accountKey === 'userio:user-b:personal');
  const checkbox = row.children.find((item) => item.dataset?.connectionId === 'work').children[0];
  checkbox.checked = true; await checkbox.listeners.change();
  const rpc = state.calls.find((call) => call.kind === 'accountBindingsSave');
  assert.equal(rpc.accountKey, 'userio:user-b:personal'); assert.deepEqual(Array.from(rpc.connectionIds).sort(), ['personal', 'work']);
});

test('adding work and personal connections emits separate save commands', async () => {
  const state = boot(); await flush();
  state.elements.connectionLabel.value = 'Рабочее'; state.elements.connectionEndpoint.value = 'https://same.test';
  await state.elements.connectionForm.listeners.submit({ preventDefault() {} });
  state.elements.connectionLabel.value = 'Личное'; state.elements.connectionEndpoint.value = 'https://same.test';
  await state.elements.connectionForm.listeners.submit({ preventDefault() {} });
  const saves = state.calls.filter((call) => call.kind === 'connectionsSave');
  assert.equal(saves.length, 2); assert.equal(saves[0].input.label, 'Рабочее'); assert.equal(saves[1].input.label, 'Личное');
  assert.equal(saves[0].input.endpoint, saves[1].input.endpoint);
});

test('per-connection OAuth and ChatGPT registration carry distinct connection ids', async () => {
  const state = boot(); await flush();
  for (const card of state.elements.connections.children) {
    const login = card.children.find((child) => child.textContent === 'Войти'); await login.listeners.click();
    const chatgpt = card.children.find((child) => child.textContent === 'Подключить ChatGPT'); await chatgpt.listeners.click();
  }
  assert.deepEqual(state.calls.filter((call) => call.kind === 'userioConnect').map((call) => call.connectionId), ['work', 'personal']);
  assert.deepEqual(state.calls.filter((call) => call.kind === 'gptRegister').map((call) => call.connectionId), ['work', 'personal']);
});

test('destination matrix renders saved A-both and B-personal bindings after reload', async () => {
  const state = boot(); await flush();
  const byKey = Object.fromEntries(state.elements.connectionAccounts.children.map((row) => [row.dataset.accountKey, row]));
  const selectedIds = (row) => row.children.filter((label) => label.dataset?.connectionId && label.children[0].checked).map((label) => label.dataset.connectionId).sort();
  assert.deepEqual(selectedIds(byKey['userio:user-a:work']), ['personal', 'work']);
  assert.deepEqual(selectedIds(byKey['userio:user-b:personal']), ['personal']);
});

test('options UI does not expose route ids or tokens', () => {
  const html = fs.readFileSync(path.join(root, 'options.html'), 'utf8');
  assert.doesNotMatch(html, /id="routeId"|access_token|refresh_token/);
});
