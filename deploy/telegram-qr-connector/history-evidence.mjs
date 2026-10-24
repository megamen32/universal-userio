import { createHash } from 'node:crypto';

// Fingerprints come from this GetHistory response, never the retained mirror.
// No plaintext, session material, read acknowledgement or send is involved.
export function historyEvidence(message, attachments = []) {
  const media = message.media;
  const mediaKind = String(media?.className || '');
  const payloadMedia = Boolean(media) && !['MessageMediaWebPage', 'MessageMediaEmpty'].includes(mediaKind);
  return {
    message_id: Number(message.id),
    date: Number(message.date),
    outgoing: message.out === true,
    body_sha256: createHash('sha256').update(String(message.message || ''), 'utf8').digest('hex'),
    has_attachments: payloadMedia || attachments.some(a => !['telegram_routing', 'webpage'].includes(a.kind)),
    has_reply_context: Boolean(message.replyTo?.replyToMsgId || message.replyTo?.replyToTopId),
    edited: Boolean(message.editDate),
  };
}
