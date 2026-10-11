import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url), "utf8");

test("native unread refresh uses read-only GetPeerDialogs and does not alter Telegram receipts", () => {
  const start = source.indexOf("async function refreshOneUnreadCount(");
  const end = source.indexOf("function scheduleUnreadCountRefresh(", start);
  const refresh = source.slice(start, end);
  assert.match(refresh, /GetPeerDialogs/);
  assert.match(refresh, /unread_count: unread/);
  assert.doesNotMatch(refresh, /ReadHistory|sendMessage|sendRead|markRead/);
});

test("read events and new incoming messages schedule bounded unread refresh", () => {
  assert.match(source, /const unreadRefreshTimers = new Map\(\)/);
  assert.match(source, /nextAllowed - Date\.now\(\)/);
  assert.match(source, /scheduleUnreadCountRefresh\(slot, client, accountId, chatKey, peer\)/);
  assert.match(source, /if \(!isEdit && !message\.out\)/);
});
