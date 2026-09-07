function idString(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "object") {
    if (value.userId !== undefined) return idString(value.userId);
    if (value.channelId !== undefined) return idString(value.channelId);
    if (value.chatId !== undefined) return idString(value.chatId);
  }
  const text = String(value);
  return text === "[object Object]" ? "" : text;
}

function entityName(entity) {
  if (!entity) return "";
  if (entity.className) return String(entity.className);
  if (entity.constructor && entity.constructor.name) return String(entity.constructor.name);
  return "";
}

function explicitSelfMention(message, selfId, selfUsername) {
  const text = String((message && message.message) || "");
  const username = String(selfUsername || "").replace(/^@/, "").toLowerCase();
  const entities = (message && Array.isArray(message.entities)) ? message.entities : [];
  for (const entity of entities) {
    const kind = entityName(entity);
    if (kind.indexOf("MessageEntityMentionName") !== -1) {
      if (idString(entity.userId) && idString(entity.userId) === String(selfId || "")) return true;
    }
    if (kind.indexOf("MessageEntityMention") !== -1 && username) {
      const offset = Number(entity.offset || 0);
      const length = Number(entity.length || 0);
      const token = text.slice(offset, offset + length).replace(/^@/, "").toLowerCase();
      if (token === username) return true;
    }
  }
  // Telegram normally supplies mention entities. Keep a deterministic text
  // fallback for older GramJS payloads where entities are absent.
  return !!username && text.toLowerCase().indexOf("@" + username) !== -1;
}

function replyMessageId(message) {
  if (!message) return "";
  const reply = message.replyTo || {};
  return idString(reply.replyToMsgId || message.replyToMsgId || "");
}

export async function telegramGroupRoutingAttachment(options) {
  const chatKey = String(options.chatKey || "");
  if (!chatKey.startsWith("-")) return null;
  const message = options.message || {};
  let sender = {};
  if (typeof options.resolveSender === "function") {
    try { sender = await options.resolveSender(); } catch (_error) { sender = {}; }
  }
  const authorId = String(sender.id || idString(message.senderId) || idString(message.fromId) || "");
  const authorName = String(sender.name || "telegram");
  const replyTo = replyMessageId(message);
  let replyToOwn = false;
  if (replyTo && typeof options.resolveReply === "function") {
    try {
      const parent = await options.resolveReply(replyTo);
      replyToOwn = !!(parent && parent.out);
    } catch (_error) { replyToOwn = false; }
  }
  const mentionSelf = explicitSelfMention(message, options.selfId, options.selfUsername);
  const metadata = {
    version: 1,
    group: true,
    chat_id: chatKey,
    group_name: String(options.groupName || chatKey),
    author_id: authorId,
    author_name: authorName,
    mention_self: mentionSelf,
    reply_to_message_id: replyTo,
    reply_to_own: replyToOwn,
    addressed: mentionSelf || replyToOwn,
  };
  return {
    kind: "telegram_routing",
    content_type: "application/vnd.userio.telegram-routing+json",
    filename: "telegram-routing.json",
    provider_ref: JSON.stringify(metadata),
  };
}
