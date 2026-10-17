export type OutgoingMessageStatus = "sent" | "read"

/** A missing read receipt stays at the sent state; only a provider receipt upgrades it. */
export function outgoingMessageStatus(
  outgoing: boolean,
  providerRead: boolean | number | null | undefined,
): OutgoingMessageStatus | null {
  if (!outgoing) return null
  return providerRead === true || providerRead === 1 ? "read" : "sent"
}
