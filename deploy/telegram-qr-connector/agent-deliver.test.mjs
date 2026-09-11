import assert from "assert";
import { buildAgentDeliverEvent } from "./agent-deliver.mjs";

const event = buildAgentDeliverEvent({
  normalizedChatId: "5453051466",
  label: "ИИ-Бенчмарки",
  envelope: {
    message_id: "-5453051466:1644",
    attachments: [{
      kind: "telegram_routing",
      provider_ref: JSON.stringify({ author_id: "16558149", author_name: "Dmitry Dubovskoy" }),
    }],
  },
  messageCount: 2,
  quietSeconds: 300,
  agentName: "secretary-excode",
  agentCwd: "/home/roomhacker/excode",
  ignoredChats: ["conv_hermes", "conv_hermes", ""],
});

assert.strictEqual(event.event_id, "telegram-quiet:5453051466:1644");
assert.match(event.subject, /Dmitry Dubovskoy/);
assert.deepEqual(event.payload.last_author, { id: "16558149", name: "Dmitry Dubovskoy" });
assert.deepEqual(event.payload.task.read_with.arguments.ignored_chats, ["conv_hermes"]);
assert.strictEqual(event.payload.task.kind, "triage_and_reply_suggestions");
assert.strictEqual(event.payload.task.instructions.length, 5);
