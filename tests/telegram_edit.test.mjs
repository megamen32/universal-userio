import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url), "utf8");

test("Telegram edit route is authenticated and pinned to one connected account", () => {
  const start = source.indexOf('url.pathname === "/edit"');
  const end = source.indexOf('url.pathname === "/react"', start);
  assert.ok(start > 0 && end > start, "POST /edit route is missing");
  const route = source.slice(start, end);
  assert.match(route, /USERIO_API_TOKEN/);
  assert.match(route, /item\.accountId === accountId/);
  assert.match(route, /slice\(0, separator\) !== accountId/);
  assert.match(route, /peerId !== chatId/);
  assert.match(route, /editOwnedTelegramText/);
  assert.match(route, /submitted: "unknown"/);
});
