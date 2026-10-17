export type MatrixChatListRow = {
  id: string
  source: string
  account_ref?: string
  peer_id?: string
  unread_count: number
  account_refs?: string[]
  account_unread_counts?: Record<string, number>
  account_conversation_ids?: Record<string, string>
}

/** Collapse only exact Matrix room copies that arrived through different accounts. */
export function groupMatrixChatRows<T extends MatrixChatListRow>(rows: T[]): (T & {
  account_refs?: string[]
  account_unread_counts?: Record<string, number>
  account_conversation_ids?: Record<string, string>
})[] {
  const groups = new Map<string, T[]>()
  for (const row of rows) {
    const isMatrix = row.source === 'matrix' || row.source.startsWith('matrix:')
    const key = isMatrix && row.peer_id
      ? `matrix-room:${row.peer_id}`
      : `conversation:${row.id}`
    const members = groups.get(key) || []
    members.push(row)
    groups.set(key, members)
  }

  const result: (T & { account_refs?: string[]; account_unread_counts?: Record<string, number>; account_conversation_ids?: Record<string, string> })[] = []
  for (const members of groups.values()) {
    const accountRefs = [...new Set(members.map(row => row.account_ref || '').filter(Boolean))]
    if (members.length < 2 || accountRefs.length < 2) {
      result.push(...members)
      continue
    }
    const accountUnread = Object.fromEntries(accountRefs.map(accountRef => [
      accountRef,
      Math.max(...members.filter(row => row.account_ref === accountRef).map(row => row.unread_count)),
    ]))
    const accountConversationIds = Object.fromEntries(accountRefs.map(accountRef => [
      accountRef,
      members.find(row => row.account_ref === accountRef)!.id,
    ]))
    result.push({
      ...members[0],
      unread_count: Math.max(...Object.values(accountUnread)),
      account_refs: accountRefs,
      account_unread_counts: accountUnread,
      account_conversation_ids: accountConversationIds,
    })
  }
  return result
}
