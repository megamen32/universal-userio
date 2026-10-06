import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// UserIO reacts to stored telegram messages through the connector. The stored
// ids look like "account|chatKey:msgId" or "chatKey:msgId", so the endpoint
// must scope off the account prefix and split chatKey from the numeric id.
const source = readFileSync(
  new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url),
  "utf8",
);

test("/react endpoint requires the same bearer token as /send", () => {
  const at = source.indexOf('url.pathname === "/react"');
  assert.ok(at > 0, "/react route is missing");
  const window_ = source.slice(at, at + 700);
  assert.match(window_, /USERIO_API_TOKEN/);
  assert.match(window_, /401/);
});

test("/react parses stored scoped message ids", () => {
  const at = source.indexOf('url.pathname === "/react"');
  const window_ = source.slice(at, at + 2000);
  assert.match(window_, /indexOf\("\|"\) \+ 1/);
  assert.match(window_, /lastIndexOf\(":"\)/);
  assert.match(window_, /Number\.isSafeInteger\(msgId\)/);
  assert.match(window_, /emoji\.length > 16/);
});

test("/react sends a Telegram SendReaction with the requested emoticon", () => {
  const at = source.indexOf('url.pathname === "/react"');
  const window_ = source.slice(at, at + 2600);
  assert.match(window_, /Api\.messages\.SendReaction/);
  assert.match(window_, /new Api\.ReactionEmoji\(\{ emoticon: emoji \}\)/);
});

test("/react keeps strict account binding like /send", () => {
  const at = source.indexOf('url.pathname === "/react"');
  const window_ = source.slice(at, at + 3000);
  assert.match(window_, /wantAccount/);
  assert.match(window_, /item\.accountId === wantAccount/);
  assert.match(window_, /does not know chat/);
});
