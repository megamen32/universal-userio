import assert from "node:assert/strict";
import test from "node:test";

import { publicIngressState } from "../deploy/telegram-qr-connector/ingress-state.mjs";

test("public ingress state identifies the live account and heartbeat", () => {
  const slots = new Map([["account-1", { status: "connected", name: "Никита", mode: "qr" }]]);
  const syncs = new Map([["account-1", {
    running: true, status: "live", chats: 100, lastError: "", lastSyncAt: 12345,
  }]]);
  const live = new Map([["account-1", { accountId: "telegram:540308572" }]]);

  assert.deepEqual(publicIngressState(slots, syncs, live), [{
    id: "account-1",
    accountId: "telegram:540308572",
    status: "connected",
    name: "Никита",
    qr: "",
    phone: "",
    mode: "qr",
    passwordHint: "",
    codeViaApp: false,
    promptSeq: 0,
    sync: "live",
    syncChats: 100,
    syncError: "",
    lastSyncAt: 12345,
  }]);
});

test("retry state exposes a bounded operator error without a fake account", () => {
  const slots = new Map([["account-2", { status: "connected" }]]);
  const syncs = new Map([["account-2", {
    running: true, status: "retrying", lastError: "session is not authorized",
  }]]);

  const state = publicIngressState(slots, syncs, new Map())[0];
  assert.equal(state.accountId, "");
  assert.equal(state.sync, "retrying");
  assert.equal(state.syncError, "session is not authorized");
});
