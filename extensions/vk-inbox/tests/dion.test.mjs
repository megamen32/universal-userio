import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

function fixture({postError = false, getError = false, state = null, accessName = "vc-access-token", legacy = false, ambiguous = false} = {}) {
  const events = [], saved = {};
  const claims = Buffer.from(JSON.stringify({user_id: 'user', org_id: 'org'})).toString('base64url');
  const cookies = [accessName, 'vc-refresh-token'].map((name, i) => ({name, value: i ? 'private-refresh' : `header.${claims}.sig`, domain: '.dion.vc', path: '/', storeId: '0'}));
  if (legacy) cookies.push({...cookies[0], name: 'vc-access-token', value: 'old-fixture'});
  if (ambiguous) cookies.push({...cookies[0], value: 'different-fixture'});
  const chrome = {
    storage: {local: {get: async defaults => ({...defaults, ...saved}), set: async values => Object.assign(saved, values)}},
    declarativeNetRequest: {updateDynamicRules: async rules => events.push(['rules', rules])},
    cookies: {getAll: async () => {events.push(['cookies']); return cookies;}, remove: async info => events.push(['remove', info.name])},
    tabs: {create: async () => events.push(['login'])},
  };
  const root = {UserIO: {
    settings: async () => ({endpoint: 'https://example.test'}),
    call: async (method, path, body) => {
      if (method === 'GET') {
        if (getError) throw new Error('UserIO GET /v1/dion/accounts: not found');
        return {accounts: state ? [{id: 'dion:org:user', state, generation: 4}] : []};
      }
      events.push(['import', body.expected_generation]);
      assert.equal(body.access_token, cookies[0].value);
      assert.equal(body.refresh_token, 'private-refresh');
      if (postError) throw new Error('unknown');
      return {accepted: true, id: 'dion:org:user', state: 'connected', generation: body.expected_generation + 1};
    },
  }};
  vm.runInNewContext(fs.readFileSync(new URL('../lib/dion.js', import.meta.url), 'utf8'), {self: root, chrome, URL, atob});
  return {api: root.UserIODion, events, saved};
}

test('blocks browser refresh before reading secrets, imports once, removes browser credentials after ack', async () => {
  const f = fixture();
  assert.equal((await f.api.importAccount('backend')).ok, true);
  assert.deepEqual(f.events.map(e => e[0]), ['rules', 'cookies', 'import', 'remove', 'remove']);
  assert.deepEqual(f.saved, {dionOwnerConnection: 'backend'});
});

test('unknown delivery retains refresh block and never restores or persists credentials', async () => {
  const f = fixture({postError: true});
  await assert.rejects(f.api.importAccount('backend'), /unknown/);
  assert.deepEqual(f.events.map(e => e[0]), ['rules', 'cookies', 'import']);
  assert.deepEqual(f.saved, {dionOwnerConnection: 'backend'});
  await assert.rejects(f.api.beginReauth('other-backend'), /another_connection/);
});

test('connected backend cannot relinquish ownership for another browser refresh', async () => {
  const f = fixture({state: 'connected'});
  await assert.rejects(f.api.beginReauth('backend'), /already_owned/);
  assert.deepEqual(f.events, []);
  await assert.rejects(f.api.importAccount('backend'), /already_owned/);
  assert.equal(f.events.some(e => e[0] === 'import'), false);
});

test('reauth imports the current generation and enables fresh login only on explicit request', async () => {
  const f = fixture({state: 'reauth_required'});
  await f.api.beginReauth('backend');
  assert.deepEqual(f.events.map(e => e[0]), ['rules', 'login']);
  await f.api.importAccount('backend');
  assert.equal(f.events.find(e => e[0] === 'import')[1], 4);
});

test('unreconciled refresh cannot return ownership to browser', async () => {
  const f = fixture({state: 'refresh_pending'});
  await assert.rejects(f.api.beginReauth('backend'), /already_owned/);
  assert.deepEqual(f.events, []);
});

test('old backend is explained before any credential access or browser mutation', async () => {
  const f = fixture({getError: true});
  await assert.rejects(f.api.importAccount('backend'), /обновление сервера/);
  assert.deepEqual(f.events, []);
  assert.deepEqual(f.saved, {});
});

test('accepts current cookie name and clears both current and legacy aliases after acknowledgement', async () => {
  const f = fixture({accessName: 'vc-authentication-token', legacy: true});
  assert.equal((await f.api.importAccount('backend')).ok, true);
  assert.deepEqual(f.events.filter(e => e[0] === 'remove').map(e => e[1]), ['vc-authentication-token', 'vc-refresh-token', 'vc-access-token']);
});

test('does not fall back to legacy when the current cookie is ambiguous', async () => {
  const f = fixture({accessName: 'vc-authentication-token', legacy: true, ambiguous: true});
  await assert.rejects(f.api.importAccount('backend'), /dion_login_required_or_ambiguous/);
  assert.equal(f.events.some(e => e[0] === 'import'), false);
});
