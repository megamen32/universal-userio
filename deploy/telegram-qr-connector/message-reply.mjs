import { formatTelegramMessage, sameTelegramEntities } from './message-format.mjs';

export class TelegramReplyRefused extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
    this.submitted = false;
  }
}

const nativeId = value => typeof value === "string" && /^[1-9][0-9]*$/.test(value)
  && Number.isSafeInteger(Number(value)) && Number(value) <= 0x7fffffff;

export async function sendTelegramReply({ client, peer, messageId, threadId = "", expectedText, text, format }) {
  if (!peer || !nativeId(messageId)) {
    throw new TelegramReplyRefused("invalid_reply_target", "A valid Telegram chat and source message id are required");
  }
  if (threadId && !nativeId(threadId)) {
    throw new TelegramReplyRefused("invalid_reply_thread", "A valid Telegram topic id is required");
  }
  if (typeof expectedText !== "string" || typeof text !== "string" || !text.trim()
      || [...text].length > 4096) {
    throw new TelegramReplyRefused("invalid_reply_text", "Telegram reply text must contain 1 to 4096 characters");
  }

  let before;
  try {
    before = (await client.getMessages(peer, { ids: [Number(messageId)] }))?.[0];
  } catch {
    throw new TelegramReplyRefused("source_unavailable", "Telegram could not verify the selected source message");
  }
  if (!before || String(before.id) !== messageId) {
    throw new TelegramReplyRefused("source_missing", "The selected Telegram message no longer exists in this chat");
  }
  if (String(before.message || "") !== expectedText) {
    throw new TelegramReplyRefused("source_changed", "The selected Telegram message changed; refresh the conversation before replying");
  }
  const sourceThreadId = String(before.replyTo?.replyToTopId || "");
  if (threadId && sourceThreadId && sourceThreadId !== threadId) {
    throw new TelegramReplyRefused("thread_changed", "The selected Telegram message moved to another topic");
  }

  const formatted = formatTelegramMessage(text, format);
  if (!formatted.text.trim() || [...formatted.text].length > 4096) {
    throw new TelegramReplyRefused('invalid_reply_text', 'Rendered Telegram text is invalid');
  }
  const options = { message: formatted.text, replyTo: Number(messageId),
    ...(format ? {formattingEntities: formatted.entities} : {}) };
  const effectiveThreadId = threadId || sourceThreadId;
  if (effectiveThreadId) options.topMsgId = Number(effectiveThreadId);
  const sent = await client.sendMessage(peer, options);
  if (!sent || !nativeId(String(sent.id || ""))) {
    throw new Error("Telegram reply outcome is unknown; send response had no message id");
  }

  const after = (await client.getMessages(peer, { ids: [Number(sent.id)] }))?.[0];
  const replyToId = String(after?.replyTo?.replyToMsgId || "");
  const returnedThreadId = String(after?.replyTo?.replyToTopId || "");
  if (!after || String(after.id) !== String(sent.id) || after.out !== true
      || String(after.message || "") !== formatted.text || replyToId !== messageId
      || format && !sameTelegramEntities(formatted.entities, after.entities)
      || effectiveThreadId && returnedThreadId !== effectiveThreadId) {
    throw new Error("Telegram reply outcome is unknown; provider readback did not confirm the exact reply");
  }
  return {
    messageId: String(after.id), text: String(after.message), replyToId,
    threadId: returnedThreadId, date: Number(after.date || 0), out: true,
  };
}
