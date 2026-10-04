import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url),
  "utf8",
);

test("live handler is attached before initial backfill", () => {
  const syncStart = source.indexOf("async function syncAccount(slot)");
  const handler = source.indexOf("client.addEventHandler(", syncStart);
  const backfill = source.indexOf("const chats = await backfillDialogs", syncStart);
  assert.ok(syncStart >= 0 && handler > syncStart && backfill > handler);
});

test("historical reconciliation cannot start Telegram media transcription", () => {
  assert.match(source, /backfillDialogs[\s\S]*transcribeAudio: false/);
  assert.match(source, /shouldTranscribe[\s\S]*transcribeTelegramAudio/);
});

test("periodic reconciliation restores public live status", () => {
  assert.match(source, /status: "live", lastError: "", lastSyncAt: Date\.now\(\)/);
});
