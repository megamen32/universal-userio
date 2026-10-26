import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url), "utf8",
);
const start = source.indexOf('url.pathname === "/button/prepare"');
const route = source.slice(start, start + 7000);

test("Telegram callback endpoint authenticates and binds one exact account and peer", () => {
  assert.ok(start > 0);
  assert.match(route, /USERIO_API_TOKEN/);
  assert.match(route, /item\.accountId === accountId/);
  assert.match(route, /idPeers\?\.get\(peerId\)/);
});

test("button press requires the current visible callback button and exact data", () => {
  assert.match(route, /item\.client\.getMessages\(peer, \{ ids: \[Number\(messageId\)\] \}\)/);
  assert.match(route, /button\.className !== "KeyboardButtonCallback"/);
  assert.match(route, /button\.text !== buttonText/);
  assert.match(route, /payload\.callback_data_base64 !== callbackDataBase64/);
  assert.match(route, /Api\.messages\.GetBotCallbackAnswer/);
});

test("ambiguous callback invocation is explicitly UNKNOWN and never replayed here", () => {
  assert.match(route, /button outcome unknown; do not replay/);
  assert.match(route, /status: "accepted"/);
  assert.match(route, /provider_receipt: providerReceipt/);
});
