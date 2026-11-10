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

function boot(discover = false) {
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
    if (message.kind === 'gptIdentity' && discover && !accounts.some(a => a.accountKey === 'chatgpt:discovered')) accounts.push({accountKey:'chatgpt:discovered',provider:'chatgpt',userId:'person',accountScope:'a'.repeat(64)});
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

test('refresh observes identity before listing accounts and renders an unbound row', async () => {
  const state = boot(true); await flush();
  assert.ok(state.calls.findIndex(x=>x.kind==='gptIdentity') < state.calls.findIndex(x=>x.kind==='accountsList'));
  const row=state.elements.connectionAccounts.children.find(x=>x.dataset.accountKey==='chatgpt:discovered');
  assert.ok(row); assert.ok(row.children.filter(x=>x.dataset?.connectionId).every(x=>!x.children[0].checked));
  assert.equal(state.calls.some(x=>x.kind==='accountBindingsSave'),false);
});

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
    const login = card.children[1].children.find((child) => child.textContent === 'Войти'); await login.listeners.click();
    const chatgpt = card.children[1].children.find((child) => child.textContent === 'Подключить ChatGPT'); await chatgpt.listeners.click();
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

test('named destinations do not show a misleading global login and long scopes stay out of account labels', async () => {
  const state = boot();
  state.accounts[1].accountScope = 'a'.repeat(64);
  await flush();
  assert.equal(state.elements.legacyUserIOCard.hidden, true);
  assert.equal(state.elements.legacyChatGPTCard.hidden, true);
  const row = state.elements.connectionAccounts.children.find(item => item.dataset.accountKey === 'chatgpt:scope-a');
  assert.equal(row.children[0].textContent, 'ChatGPT A · chatgpt');
  assert.equal(row.children[0].title, 'a'.repeat(64));
  assert.match(state.html, /\.connection-actions\s*\{[^}]*flex-wrap:\s*wrap/s);
  assert.match(state.html, /\.binding-choice\s*\{[^}]*white-space:\s*nowrap/s);
});

test('legacy destination is explicit and all connection statuses are scoped', async () => {
  const state = boot();
  state.connections.push({id:'legacy-default',label:'UserIO',endpoint:'https://legacy.test',enabled:true});
  await flush();
  const legacy = state.elements.connections.children.find(row => row.dataset.connectionId === 'legacy-default');
  assert.equal(legacy.children[0].children[0].textContent, 'Основное подключение (ранее настроенное)');
  assert.equal(legacy.children[0].children[2].textContent, 'Подключено: Пользователь');
  assert.ok(state.calls.some(call => call.kind === 'userioStatus' && call.connectionId === 'legacy-default'));
});

test('ChatGPT account management opens for the chosen destination', async () => {
  const state = boot(); await flush();
  const card = state.elements.connections.children.find(row => row.dataset.connectionId === 'personal');
  const manage = card.children[1].children.find(child => child.textContent === 'Аккаунты ChatGPT');
  await manage.listeners.click();
  assert.equal(state.elements.legacyChatGPTCard.hidden, false);
  assert.equal(state.elements.chatgptCardTitle.textContent, 'ChatGPT · Личный');
  assert.equal(state.elements.chatgptSelect.dataset.connectionId, 'personal');
  assert.ok(state.calls.some(call => call.kind === 'gptConnectionStatus' && call.connectionId === 'personal'));
});
