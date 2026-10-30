import assert from "node:assert/strict";
import test from "node:test";
import { sendTelegramReply } from "./message-reply.mjs";

test("sends to the exact Telegram peer and replies to the selected source and topic", async () => {
  const calls = [];
  const source = { id: 77, message: "source", replyTo: { replyToTopId: 70 } };
  let sent;
  const client = {
    async sendMessage(peer, options) {
      calls.push({ peer, options });
      sent = { id: 78, message: options.message, date: 123, out: true,
        replyTo: { replyToMsgId: options.replyTo, replyToTopId: options.topMsgId } };
      return sent;
    },
    async getMessages(_peer, { ids }) {
      return [ids[0] === 77 ? source : sent];
    },
  };
  const result = await sendTelegramReply({
    client, peer: "exact-peer", messageId: "77", threadId: "70", expectedText: "source", text: "answer",
  });
  assert.deepEqual(calls, [{ peer: "exact-peer", options: {
    message: "answer", replyTo: 77, topMsgId: 70,
  } }]);
  assert.deepEqual(result, {
    messageId: "78", text: "answer", replyToId: "77", threadId: "70", date: 123, out: true,
  });
});

test('sends and reads back a formatted reply without changing its source binding', async () => {
  let sent, options;
  const source = {id: 77, message: 'source'};
  const client = {
    async sendMessage(_peer, value) {
      options = value;
      sent = {id: 78, message: value.message, entities: value.formattingEntities,
        date: 123, out: true, replyTo: {replyToMsgId: value.replyTo}};
      return sent;
    },
    async getMessages(_peer, {ids}) { return [ids[0] === 77 ? source : sent]; },
  };
  const result = await sendTelegramReply({client, peer: 'peer', messageId: '77',
    expectedText: 'source', text: '**Готово**', format: 'telegram_markdown'});
  assert.equal(options.message, 'Готово');
  assert.equal(options.formattingEntities[0].className, 'MessageEntityBold');
  assert.equal(result.text, 'Готово');
});

test("refuses invalid reply ids before invoking Telegram", async () => {
  let called = false;
  await assert.rejects(sendTelegramReply({
    client: { async sendMessage() { called = true; } },
    peer: "peer", messageId: "not-an-id", threadId: "", expectedText: "source", text: "answer",
  }), /valid Telegram chat and source message id/);
  assert.equal(called, false);
});

test("refuses a source changed on Telegram before sending", async () => {
  let sent = false;
  const client = {
    async getMessages() { return [{ id: 77, message: "new source" }]; },
    async sendMessage() { sent = true; },
  };
  await assert.rejects(sendTelegramReply({
    client, peer: "peer", messageId: "77", expectedText: "old source", text: "answer",
  }), error => error.code === "source_changed" && error.submitted === false);
  assert.equal(sent, false);
});
