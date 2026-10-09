// Telegram native timestamps/direction/read maxima, independent from UI visits.
export function mirrorMetadata(message, dialog) {
  const direction = message.out ? "outgoing" : "incoming";
  const result = { direction };
  const date = Number(message.date);
  if (Number.isFinite(date) && date > 0) result.received_at = date;
  if (dialog) {
    const maxId = Number(message.out ? dialog.readOutboxMaxId : dialog.readInboxMaxId);
    if (Number.isSafeInteger(maxId) && maxId >= 0) result.provider_read = Number(message.id) <= maxId;
  }
  return result;
}

export function confirmedDeletedIds(requestedIds, messages) {
  // getMessages(ids) must succeed first. A failed/short history is never used.
  if (!Array.isArray(messages) || messages.length !== requestedIds.length) return [];
  const byId = new Map(messages.filter(m => m && Number.isSafeInteger(Number(m.id)))
    .map(m => [Number(m.id), m]));
  if (!requestedIds.every(id => byId.has(id))) return [];
  return requestedIds.filter(id => byId.get(id).className === "MessageEmpty");
}
