/** Preserve explicitly loaded older calls when a normal inbox poll refreshes the latest page. */
export function mergePhoneHistory<T extends { source: string; message_id: string; received_at: number }>(older: T[], latest: T[]): T[] {
  const byId = new Map([...older, ...latest].map(message => [JSON.stringify([message.source, message.message_id]), message]))
  return [...byId.values()].sort((a, b) => a.received_at - b.received_at || (a.message_id < b.message_id ? -1 : a.message_id > b.message_id ? 1 : 0))
}
