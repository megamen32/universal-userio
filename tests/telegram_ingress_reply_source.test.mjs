import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { duplicateReceipt, replyAttemptFields } from "../deploy/telegram-qr-connector/send-receipt.mjs";

const source = readFileSync(
  new URL("../deploy/telegram-qr-connector/server.mjs", import.meta.url),
  "utf8",
);

test("a repeated reply answers with the source it is bound to", () => {
  const receipt = duplicateReceipt({
    slot: "account-7", account_id: "telegram:900000101", peer_id: "900000300",
    message_id: "78", date: 1700000100, kind: "reply", reply_to_id: "77", thread_id: "70",
  });
  assert.equal(receipt.ok, true);
  assert.equal(receipt.duplicate, true);
  assert.equal(receipt.message_id, "78");
  assert.equal(receipt.reply_to_id, "77");
  assert.equal(receipt.thread_id, "70");
});

test("a repeated plain send carries no reply binding", () => {
  const receipt = duplicateReceipt({
    slot: "account-7", account_id: "telegram:900000101", peer_id: "900000300",
    message_id: "55", date: 1700000100,
  });
  assert.equal(receipt.ok, true);
  assert.equal(receipt.duplicate, true);
  assert.equal(receipt.message_id, "55");
  assert.equal("reply_to_id" in receipt, false);
  assert.equal("thread_id" in receipt, false);
});

test("a reply without a topic still names an empty topic", () => {
  const receipt = duplicateReceipt({
    slot: "account-7", account_id: "telegram:900000101", peer_id: "900000300",
    message_id: "78", date: 1700000100, kind: "reply", reply_to_id: "77",
  });
  assert.equal(receipt.reply_to_id, "77");
  assert.equal(receipt.thread_id, "");
});

test("the journal keeps the source of a reply", () => {
  assert.deepEqual(replyAttemptFields({ replyToId: "77", threadId: "70" }),
    { reply_to_id: "77", thread_id: "70" });
  assert.deepEqual(replyAttemptFields({ replyToId: "77", threadId: "" }),
    { reply_to_id: "77", thread_id: "" });
});

test("the send route answers a repeated approval from the journal", () => {
  const start = source.indexOf('url.pathname === "/send"');
  const route = source.slice(start, source.indexOf('url.pathname === "/upload-limit"', start));
  assert.ok(start > 0, "POST /send route is missing");
  assert.match(route, /duplicateReceipt\(recorded\)/);
  assert.match(route, /\.\.\.replyAttemptFields\(sent\)/);
});
