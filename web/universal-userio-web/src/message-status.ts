export type OutgoingMessageStatus = "sent" | "read"

/** A missing read receipt stays at the sent state; only a provider receipt upgrades it. */
export function outgoingMessageStatus(
  source: string,
  outgoing: boolean,
  providerRead: boolean | number | null | undefined,
): OutgoingMessageStatus | null {
  const provider = source.startsWith("matrix:") ? "matrix" : source
  if (!outgoing || !["telegram", "whatsapp", "max", "vk", "matrix", "sms"].includes(provider)) return null
  return providerRead === true || providerRead === 1 ? "read" : "sent"
}
