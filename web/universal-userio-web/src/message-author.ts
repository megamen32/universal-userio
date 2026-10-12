type AuthorMessage = { source: string; sender: string; sender_name?: string; attachments?: unknown[] }
export const messageAuthor = (message: AuthorMessage, kind?: string): string => {
  if (message.source === 'telegram') {
    for (const item of message.attachments || []) {
      if (!item || typeof item !== 'object') continue
      const attachment = item as {kind?: string; provider_ref?: string}
      if (attachment.kind !== 'telegram_routing') continue
      try {
        const author = JSON.parse(attachment.provider_ref || '{}')
        if (author.group !== true) continue
        const name = String(author.author_name || '').trim()
        const username = String(author.author_username || '').trim().replace(/^@/, '')
        const label = name && (name !== author.group_name || author.author_resolved === true) ? name : ''
        return [label, username ? '@' + username : ''].filter(Boolean).join(' · ')
          || (author.author_id ? 'Участник ' + author.author_id : 'Автор неизвестен')
      } catch { /* Missing metadata must not hide the message. */ }
    }
  }
  if (kind !== 'group' && kind !== 'channel') return ''
  return message.sender_name?.trim() || (message.sender ? 'Участник ' + message.sender : 'Автор неизвестен')
}
