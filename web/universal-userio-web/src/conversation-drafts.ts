type Draft = { status: string }

export type ConversationTimelineEntry<M, D> =
  | { kind: "message"; item: M; index: number }
  | { kind: "draft"; item: D }

/** Delivery records are not messages, including when their source page is unloaded. */
export function conversationDrafts<T extends Draft>(conversation: { drafts: T[] }): T[] {
  return conversation.drafts.filter(item => item.status !== "approved" && item.status !== "rejected")
}

/** Treat pending drafts as timeline entries, ordered only by their creation time. */
export function conversationTimeline<
  M extends { received_at: number },
  D extends { created_at?: number },
>(messages: M[], drafts: D[]): ConversationTimelineEntry<M, D>[] {
  const entries: Array<{ entry: ConversationTimelineEntry<M, D>; at: number; tie: number; order: number }> = [
    ...messages.map((item, index) => ({
      entry: { kind: "message" as const, item, index }, at: item.received_at, tie: 0, order: index,
    })),
    ...drafts.map((item, index) => ({
      entry: { kind: "draft" as const, item }, at: item.created_at ?? Number.POSITIVE_INFINITY, tie: 1, order: index,
    })),
  ]
  return entries.sort((a, b) => a.at - b.at || a.tie - b.tie || a.order - b.order).map(item => item.entry)
}
