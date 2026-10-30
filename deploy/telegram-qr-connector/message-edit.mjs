import { formatTelegramMessage, sameTelegramEntities } from './message-format.mjs';

export class TelegramEditRefused extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
    this.submitted = false;
  }
}

export async function editOwnedTelegramText({ client, api, peer, messageId, expectedText, text, format }) {
  if (!Number.isSafeInteger(messageId) || messageId <= 0) {
    throw new TelegramEditRefused("invalid_message_id", "A valid Telegram message id is required");
  }
  if (typeof expectedText !== "string" || typeof text !== "string" || !text.trim()) {
    throw new TelegramEditRefused("invalid_text", "Expected and replacement text are required");
  }
  if (format === undefined && expectedText === text) {
    throw new TelegramEditRefused("unchanged_text", "The replacement text is unchanged");
  }

  const before = (await client.getMessages(peer, { ids: [messageId] }))?.[0];
  if (!before) throw new TelegramEditRefused("message_not_found", "Telegram message was not found");
  if (before.out !== true) throw new TelegramEditRefused("not_own_message", "Only your own Telegram message can be edited");
  if (String(before.message || "") !== expectedText) {
    throw new TelegramEditRefused("source_changed", "Telegram message changed; refresh the conversation before editing");
  }
  const mediaType = String(before.media?.className || "");
  if (mediaType && mediaType !== "MessageMediaWebPage") {
    throw new TelegramEditRefused("media_edit_not_supported", "Only text messages can be edited here");
  }

  // The official MTProto request is messages.editMessage. Read the message
  // back after submission so the mirror only changes on provider confirmation.
  const formatted = formatTelegramMessage(text, format);
  if (!formatted.text.trim() || [...formatted.text].length > 4096) {
    throw new TelegramEditRefused('invalid_text', 'Rendered Telegram text is invalid');
  }
  await client.invoke(new api.messages.EditMessage({
    peer, id: messageId, message: formatted.text, noWebpage: false,
    ...(format ? {entities: formatted.entities} : {}),
  }));
  const after = (await client.getMessages(peer, { ids: [messageId] }))?.[0];
  if (!after || after.out !== true || String(after.message || "") !== formatted.text
      || format && !sameTelegramEntities(formatted.entities, after.entities)) {
    throw new Error("Telegram edit outcome is unknown; readback did not confirm the replacement");
  }
  return {
    messageId: String(after.id),
    text: String(after.message),
    editDate: Number(after.editDate || 0),
    out: true,
  };
}
