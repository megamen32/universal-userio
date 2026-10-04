import assert from "node:assert/strict";
import test from "node:test";

import {
  loginAuthorized,
  normalizeLoginCode,
  normalizeLoginPhone,
} from "../deploy/telegram-qr-connector/login-api.mjs";

test("operator login API requires the exact UserIO bearer", () => {
  assert.equal(loginAuthorized({ authorization: "Bearer exact" }, "exact"), true);
  assert.equal(loginAuthorized({ authorization: "Bearer wrong" }, "exact"), false);
  assert.equal(loginAuthorized({}, ""), false);
});

test("phone login accepts normalized international numbers only", () => {
  assert.equal(normalizeLoginPhone("+7 (999) 000-11-22"), "+79990001122");
  assert.throws(() => normalizeLoginPhone("89990001122"), /international/);
});

test("login code is bounded and numeric", () => {
  assert.equal(normalizeLoginCode("123 45"), "12345");
  assert.throws(() => normalizeLoginCode("12ab"), /invalid/);
});
