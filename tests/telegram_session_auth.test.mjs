import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { authRetryDelaySeconds, classifyAuthError, probeAuthorization } from "../deploy/telegram-qr-connector/session-auth.mjs";

// Official TL auth errors (core.telegram.org/schema/mtproto) plus the GramJS
// client surface. The boolean isUserAuthorized() collapses all of them to
// false, which is how a revoked session became an endless retry loop.
const GetState = class {};

test("a revoked auth key is terminal, not a network hiccup", () => {
  const verdict = classifyAuthError({ errorMessage: "AUTH_KEY_UNREGISTERED" });
  assert.equal(verdict.authorized, false);
  assert.equal(verdict.terminal, true);
  assert.equal(verdict.code, "revoked_auth_key");
  assert.equal(verdict.provider_code, "AUTH_KEY_UNREGISTERED");
});

test("duplicate and expired sessions are terminal too", () => {
  for (const code of ["AUTH_KEY_DUPLICATED", "AUTH_KEY_INVALID", "SESSION_REVOKED", "SESSION_EXPIRED"]) {
    assert.equal(classifyAuthError({ errorMessage: code }).terminal, true, code);
  }
});

test("a required 2FA password is a login action, not a revoked key", () => {
  const verdict = classifyAuthError({ errorMessage: "SESSION_PASSWORD_NEEDED" });
  assert.equal(verdict.terminal, true);
  assert.equal(verdict.code, "session_password_needed");
});

test("transport and flood errors stay retryable", () => {
  for (const code of ["TIMEOUT", "FLOOD_WAIT", "INTERNAL_SERVER_ERROR", ""]) {
    const verdict = classifyAuthError(code ? { errorMessage: code } : {});
    assert.equal(verdict.authorized, false);
    assert.equal(verdict.terminal, false, code);
  }
});

test("an unknown RPC error is never treated as a reason to stop reconnecting", () => {
  const verdict = classifyAuthError({ errorMessage: "SOME_NEW_SERVER_ERROR" });
  assert.equal(verdict.terminal, false);
  assert.equal(verdict.provider_code, "SOME_NEW_SERVER_ERROR");
});

test("probeAuthorization keeps the real provider answer", async () => {
  const ok = await probeAuthorization({ invoke: async () => ({ pts: 5 }) }, { updates: { GetState } });
  assert.deepEqual(ok, { authorized: true, terminal: false, code: "authorized", provider_code: "" });
  const refused = await probeAuthorization(
    { invoke: async () => { throw { errorMessage: "AUTH_KEY_UNREGISTERED" }; } },
    { updates: { GetState } },
  );
  assert.equal(refused.terminal, true);
  assert.equal(refused.code, "revoked_auth_key");
});

test("a terminal session waits long instead of retrying every 30 seconds", () => {
  assert.equal(authRetryDelaySeconds(30, { terminal: false }), 30);
  assert.equal(authRetryDelaySeconds(30, { terminal: true }), 900);
  assert.equal(authRetryDelaySeconds(5, { terminal: true }), 900);
});

// The refusal shape is what lets UserIO release its durable claim instead of
// leaving the draft in an ambiguous sending state.
const source = readFileSync(
  new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url),
  "utf8",
);
const sendRoute = source.slice(source.indexOf('url.pathname === "/send"'), source.indexOf('url.pathname === "/react"'));

test("/send names the reason and states that nothing was submitted", () => {
  assert.match(sendRoute, /account_relogin_required/);
  assert.match(sendRoute, /account_not_connected/);
  assert.match(sendRoute, /chat_not_known/);
  assert.ok((sendRoute.match(/submitted: false/g) || []).length >= 3);
});

test("/send never falls back to another account when one is pinned", () => {
  assert.match(sendRoute, /const candidates = wantAccount \|\| wantSlot \? \[\] : \[\.\.\.liveSlots\]/);
  assert.match(sendRoute, /live\.accountId === wantAccount/);
});

test("/send reports an unexpected failure as an unknown outcome", () => {
  assert.match(sendRoute, /code: "send_outcome_unknown", submitted: "unknown"/);
});

test("the sync loop classifies the real authorization answer", () => {
  const sync = source.slice(source.indexOf("async function syncAccount"), source.indexOf("// --- operator surface"));
  assert.match(sync, /probeAuthorization\(client, Api\)/);
  assert.match(sync, /relogin-required/);
  assert.ok(!sync.includes("await client.isUserAuthorized()"), "the lossy boolean must be gone");
  assert.match(sync, /authRetryDelaySeconds\(\s*syncRetrySeconds/);
});