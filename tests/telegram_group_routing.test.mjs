import assert from "node:assert/strict";
import test from "node:test";
import { telegramGroupRoutingAttachment } from "../deploy/telegram-qr-connector/group-routing.mjs";

test("private messages have no group routing attachment", async () => {
  assert.equal(await telegramGroupRoutingAttachment({ chatKey: "540308572", message: {} }), null);
});

test("explicit username mention addresses the secretary", async () => {
  const att = await telegramGroupRoutingAttachment({
    chatKey: "-1001", groupName: "Team", selfId: "8810909089", selfUsername: "deus_excode",
    message: { message: "@deus_excode привет", senderId: "42", entities: [{ className: "MessageEntityMention", offset: 0, length: 12 }] },
    resolveSender: async () => ({ id: "42", name: "Гриша" }),
  });
  const meta = JSON.parse(att.provider_ref);
  assert.equal(meta.addressed, true);
  assert.equal(meta.mention_self, true);
  assert.equal(meta.author_id, "42");
  assert.equal(meta.author_name, "Гриша");
});

test("reply to secretary outgoing message addresses it", async () => {
  const att = await telegramGroupRoutingAttachment({
    chatKey: "-1002", groupName: "Other", selfId: "8810909089", selfUsername: "deus_excode",
    message: { message: "а это?", senderId: "77", replyTo: { replyToMsgId: 55 } },
    resolveSender: async () => ({ id: "77", name: "Alice" }),
    resolveReply: async (id) => ({ out: id === "55" }),
  });
  const meta = JSON.parse(att.provider_ref);
  assert.equal(meta.addressed, true);
  assert.equal(meta.reply_to_own, true);
  assert.equal(meta.reply_to_message_id, "55");
});

test("ordinary group noise is explicitly not addressed", async () => {
  const att = await telegramGroupRoutingAttachment({
    chatKey: "-1003", groupName: "Noise", selfId: "8810909089", selfUsername: "deus_excode",
    message: { message: "просто сообщение", senderId: "88" },
    resolveSender: async () => ({ id: "88", name: "Bob" }),
  });
  const meta = JSON.parse(att.provider_ref);
  assert.equal(meta.addressed, false);
  assert.equal(meta.mention_self, false);
  assert.equal(meta.reply_to_own, false);
});

test("channel-style messages fall back to the group name for deterministic author text", async () => {
  const att = await telegramGroupRoutingAttachment({
    chatKey: "-1004", groupName: "Channel Chat", selfId: "8810909089", selfUsername: "deus_excode",
    message: { message: "@deus_excode ping" },
    resolveSender: async () => ({ id: "", name: "" }),
  });
  const meta = JSON.parse(att.provider_ref);
  assert.equal(meta.author_name, "Channel Chat");
});
