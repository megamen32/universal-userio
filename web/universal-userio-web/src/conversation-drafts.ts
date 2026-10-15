type Draft = { status: string }

/** Delivery records are not messages, including when their source page is unloaded. */
export function conversationDrafts<T extends Draft>(conversation: { drafts: T[] }): T[] {
  return conversation.drafts.filter(item => item.status !== "approved" && item.status !== "rejected")
}
