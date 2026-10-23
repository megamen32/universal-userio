// A forwarded message carries the place it came from. That origin is content:
// without it the owner hears a message with no idea where it was taken from,
// and an agent answers as if the message had been written here.

function peerIdentifier(peer) {
  if (!peer) return "";
  const value = peer.channelId ?? peer.userId ?? peer.chatId ?? "";
  return value === undefined || value === null ? "" : String(value);
}

export function forwardOrigin(message) {
  const header = message && message.fwdFrom;
  if (!header) return null;
  const saved = !header.fromId && !header.fromName
    && Boolean(header.savedFromId || header.savedFromName);
  const name = String(header.fromName || header.savedFromName || "").trim();
  const peerId = peerIdentifier(header.fromId || header.savedFromId);
  if (!name && !peerId) return null;
  return { name, peerId, saved };
}

export function forwardLine(origin) {
  if (!origin) return "";
  const who = origin.name || (origin.peerId ? `источник ${origin.peerId}` : "");
  if (!who) return "";
  return origin.saved ? `Сохранено от: ${who}` : `Переслано от: ${who}`;
}

// The body keeps the origin, so every consumer of a message (the view, the
// search, an agent, the voice) knows where the message came from.
export function bodyWithOrigin(body, origin, limit = 8000) {
  const line = forwardLine(origin);
  const text = String(body || "");
  if (!line) return text.slice(0, limit);
  const room = Math.max(0, limit - line.length - 2);
  return `${line}\n\n${text.slice(0, room)}`.slice(0, limit);
}
