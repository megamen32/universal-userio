export const canEditMaxText = (attachments?: unknown[]) => (attachments || []).every(item =>
  !!item && typeof item === 'object' && (item as {kind?: string}).kind === 'share')

export const providerPeerLabel = (sender: string, source?: string) => {
  if (!/^-?\d+$/.test(sender)) return sender
  return source === 'max' ? `MAX:${sender}` : source === 'telegram' ? `tg:${sender}` : sender
}
