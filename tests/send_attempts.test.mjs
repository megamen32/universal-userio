import assert from "node:assert/strict";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { SendAttemptLedger, attemptState } from "../deploy/telegram-qr-connector/send-attempts.mjs";

// The connector owns every Telegram session, so it is the only process that
// can say whether an approved message reached the provider. Its journal binds
// an attempt id to the provider message id, to a refusal before provider IO,
// or to the honest in-flight state, and it makes an approved send idempotent.

function ledgerPath() {
  const dir = mkdtempSync(join(tmpdir(), "userio-send-attempts-"));
  return join(dir, "send-attempts.json");
}

test("an attempt is journalled as in flight before the provider call", () => {
  const path = ledgerPath();
  const ledger = new SendAttemptLedger(path);
  ledger.record("draft_1", { state: "pending", slot: "account-1", account_id: "telegram:540308572" });
  const entry = ledger.get("draft_1");
  assert.equal(entry.state, "pending");
  assert.equal(entry.attempt_id, "draft_1");
  assert.equal(entry.slot, "account-1");
});

test("a delivered attempt keeps its provider message id", () => {
  const path = ledgerPath();
  const ledger = new SendAttemptLedger(path);
  ledger.record("draft_1", { state: "pending" });
  ledger.record("draft_1", { state: "sent", slot: "account-1", message_id: "306" });
  assert.equal(ledger.get("draft_1").message_id, "306");
  assert.equal(attemptState(ledger.get("draft_1")), "sent");
});

test("the journal survives a connector restart", () => {
  const path = ledgerPath();
  const first = new SendAttemptLedger(path);
  first.record("draft_1", { state: "sent", slot: "account-1", message_id: "306" });
  const second = new SendAttemptLedger(path);
  assert.equal(second.get("draft_1").message_id, "306");
  assert.equal(second.get("draft_1").slot, "account-1");
});

test("an unknown attempt is absent and carries the journal epoch", () => {
  const ledger = new SendAttemptLedger(ledgerPath());
  assert.equal(ledger.get("draft_missing"), null);
  assert.equal(attemptState(null), "absent");
  assert.ok(ledger.startedAt > 0);
});

test("the journal keeps only ids and never a message body", () => {
  const path = ledgerPath();
  const ledger = new SendAttemptLedger(path);
  ledger.record("draft_1", { state: "pending" });
  const raw = readFileSync(path, "utf8");
  assert.ok(!raw.includes("body"));
  assert.ok(raw.includes("draft_1"));
});

test("the journal is bounded", () => {
  const ledger = new SendAttemptLedger(ledgerPath(), { limit: 3 });
  for (let index = 0; index < 6; index += 1) {
    ledger.record(`draft_${index}`, { state: "sent", message_id: String(index) });
  }
  assert.equal(ledger.entries.size, 3);
  assert.equal(ledger.get("draft_0"), null);
  assert.ok(ledger.get("draft_5"));
});

test("an attempt without an id is never journalled", () => {
  const ledger = new SendAttemptLedger(ledgerPath());
  assert.equal(ledger.record("", { state: "sent" }), null);
  assert.equal(ledger.entries.size, 0);
});
