export type InboxFolder = { id: string; name: string; conversations: string[] }
export type FolderChat = { id: string; source: string; unread_count: number; analysis_policy?: { conversation_kind: string } }
export const FOLDERS_KEY = 'userio-inbox-folders-v1'

export function readInboxFolders(raw: string | null): InboxFolder[] {
  try {
    const value: unknown = JSON.parse(raw || '[]')
    if (!Array.isArray(value)) return []
    const used = new Set<string>()
    return value.filter((item): item is InboxFolder => {
      if (!item || typeof item !== 'object') return false
      const row = item as Partial<InboxFolder>
      if (typeof row.id !== 'string' || !row.id || ['all', 'unread', 'personal'].includes(row.id) || used.has(row.id)) return false
      if (typeof row.name !== 'string' || !row.name.trim() || row.name.length > 60 || !Array.isArray(row.conversations) || !row.conversations.every(id => typeof id === 'string')) return false
      used.add(row.id)
      return true
    }).slice(0, 64).map(row => ({ ...row, conversations: [...new Set(row.conversations)].slice(0, 100) }))
  } catch { return [] }
}

export function inInboxFolder(chat: FolderChat, selected: string, folders: InboxFolder[]): boolean {
  if (selected === 'all') return true
  if (selected === 'unread') return chat.unread_count > 0
  if (selected === 'personal') return chat.analysis_policy?.conversation_kind === 'direct' || ['sms', 'phone'].includes(chat.source)
  return folders.find(folder => folder.id === selected)?.conversations.includes(chat.id) ?? false
}
