import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function load(relative, context) {
  vm.runInNewContext(fs.readFileSync(path.join(root, relative), 'utf8'), context, { filename: relative });
}

test('UserIO OAuth uses the Chrome callback, PKCE, account confirmation and REST readback', async () => {
  const values = { endpoint: 'https://userio.example' };
  const requests = [];
  let launchedUrl;
  const chrome = {
    identity: {
      getRedirectURL: (path) => `https://extension-id.chromiumapp.org/${path}`,
      launchWebAuthFlow: async ({ url, interactive }) => {
        assert.equal(interactive, true);
        launchedUrl = url;
        const query = new URL(url).searchParams;
        assert.equal(query.get('redirect_uri'), 'https://extension-id.chromiumapp.org/userio');
        assert.equal(query.get('scope'), 'userio');
        assert.equal(query.get('code_challenge_method'), 'S256');
        return `${query.get('redirect_uri')}?code=fixture-code&state=${query.get('state')}`;
      },
    },
    storage: { local: {
      get: async (defaults) => Array.isArray(defaults) ? Object.fromEntries(defaults.filter(k => k in values).map(k => [k, values[k]])) : ({ ...defaults, ...values }),
      set: async (next) => Object.assign(values, next),
      remove: async (keys) => { for (const key of keys) delete values[key]; },
    } },
  };
  const fetch = async (url, init = {}) => {
    requests.push({ url: String(url), init });
    if (String(url).endsWith('/register')) return response(201, { client_id: 'public-client' });
    if (String(url).endsWith('/token')) return response(200, {
      access_token: 'access-fixture', refresh_token: 'refresh-fixture', expires_in: 3600, token_type: 'Bearer', scope: 'userio',
    });
    if (String(url).endsWith('/v1/inbox')) {
      assert.equal(new Headers(init.headers).get('authorization'), 'Bearer access-fixture');
      return response(200, { items: [] });
    }
    if (String(url).endsWith('/v1/connection-principal')) return response(200, { username: 'test-user' });
    throw new Error(`unexpected URL ${url}`);
  };
  const ctx = { self: {}, chrome, fetch, URL, URLSearchParams, crypto: globalThis.crypto, TextEncoder, Headers, btoa, console };
  load('lib/oauth.js', ctx);
  const connected = await ctx.self.UserIOOAuth.connect();
  assert.equal(connected.ok, true);
  assert.equal(connected.accountLabel, 'test-user');
  assert.equal(values.oauthAccessToken, 'access-fixture');
  assert.equal(values.oauthRefreshToken, 'refresh-fixture');
  assert.ok(launchedUrl);
  await ctx.self.UserIOOAuth.connect();
  assert.equal(requests.filter(request => request.url.endsWith('/register')).length, 1);
  const tokenRequest = requests.find((request) => request.url.endsWith('/token'));
  assert.match(String(tokenRequest.init.body), /code_verifier=/);
  assert.ok(requests.some((request) => request.url.endsWith('/v1/inbox')));
  values.oauthExpiresAt = Date.now() - 1;
  assert.equal(await ctx.self.UserIOOAuth.accessToken(), 'access-fixture');
  assert.ok(requests.some((request) => String(request.init.body).includes('grant_type=refresh_token')));
  values.endpoint = 'https://another-userio.example';
  assert.equal(await ctx.self.UserIOOAuth.accessToken(), '');
  assert.equal(values.oauthAccessToken, undefined);
});

test('ChatGPT cookies are never posted by the remote action without an explicit local choice', async () => {
  const sent = [];
  const userioPosts = [];
  let sessionCookie = 'fixture-cookie';
  let accountEmail = 'fixture@example.test';
  let serviceCookie = 'first';
  const chrome = {
    cookies: { getAll: async () => [{ name: '__Secure-next-auth.session-token', value: sessionCookie }, { name: 'service', value: serviceCookie }] },
    runtime: { getPlatformInfo: async () => ({ os: 'test' }), getManifest: () => ({ version: 'test' }) },
    storage: { local: { get: async (defaults) => ({ ...defaults, endpoint: 'https://userio.example', agentId: 'test' }) } },
  };
  const ctx = {
    self: { UserIO: { settings: async () => ({ agentId: 'legacy', agentInstanceId: 'browser-11111111-1111-4111-8111-111111111111' }), call: async (_method, url, body) => { userioPosts.push({ url: 'https://userio.example' + url, init: { body: JSON.stringify(body) } }); return { accepted: true, account_id: "chatgpt:fixture" }; } } },
    chrome, navigator: { userAgent: 'fixture-agent' }, URL, URLSearchParams,
    crypto: globalThis.crypto, TextEncoder,
    fetch: async (url, init) => {
      sent.push({ url, init });
      return response(200, { user: { email: accountEmail, name: 'Fixture' } });
    },
  };
  load('lib/chatgpt.js', ctx);
  const denied = await ctx.self.ChatGPT.register();
  assert.equal(denied.ok, false);
  assert.equal(userioPosts.length, 0);
  const selected = await ctx.self.ChatGPT.describe();
  accountEmail = 'other@example.test';
  const changed = await ctx.self.ChatGPT.register({ userInitiated: true, expectedFingerprint: selected.fingerprint });
  assert.equal(changed.ok, false);
  assert.equal(userioPosts.length, 0);
  accountEmail = 'fixture@example.test';
  serviceCookie = 'rotated';
  const allowed = await ctx.self.ChatGPT.register({ userInitiated: true, expectedFingerprint: selected.fingerprint });
  assert.equal(allowed.ok, true);
  assert.equal(userioPosts.length, 1);
  assert.equal(userioPosts[0].url, 'https://userio.example/v1/chatgpt/sessions');
  assert.equal(JSON.parse(userioPosts[0].init.body).agent_id, 'browser-11111111-1111-4111-8111-111111111111');
});

function response(status, body) {
  return { ok: status < 400, status, json: async () => body };
}
