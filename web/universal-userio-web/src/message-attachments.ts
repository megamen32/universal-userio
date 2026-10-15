export const MAX_UPLOAD_BYTES = 2 * 1024 * 1024

export type Attachment = {
  index: number
  kind: string
  filename: string
  size?: number
  contentType: string
  src?: string
}

const text = (value: unknown) => typeof value === 'string' ? value : ''

export function messageAttachments(items?: unknown[]): Attachment[] {
  return (items || []).flatMap((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return []
    const data = item as Record<string, unknown>
    const kind = (text(data.kind) || text(data._type)).toLowerCase()
    // A shared link is part of the text, not a downloadable file.
    if (kind === 'share') return []
    const size = Number(data.size ?? data.file_size)
    return [{
      index, kind,
      filename: text(data.filename) || text(data.name) || text(data.fileName) || (kind === 'sticker' ? 'Стикер' : /^(image|photo)$/.test(kind) ? 'Фото' : 'Вложение'),
      ...(Number.isFinite(size) && size >= 0 ? { size } : {}),
      contentType: text(data.content_type) || text(data.mime_type) || text(data.mimeType),
      ...(text(data.src) ? { src: text(data.src) } : {}),
    }]
  })
}

export const isImageAttachment = (attachment: Attachment) =>
  /^(image|photo)$/.test(attachment.kind) || attachment.contentType.startsWith('image/') ||
  ((!attachment.contentType || attachment.contentType === 'application/octet-stream') && /\.(?:png|jpe?g|webp|gif|avif|bmp)$/i.test(attachment.filename))

export const attachmentMediaPath = (conversationId: string, messageId: string, index: number, raw = false) =>
  `/v1/conversations/${encodeURIComponent(conversationId)}/media/${encodeURIComponent(messageId)}${raw ? '/raw' : ''}?attachment_index=${index}`

export const uploadValidationError = (size: number) => size <= 0
  ? 'Файл пустой. Выберите другой файл.'
  : size > MAX_UPLOAD_BYTES ? 'Файл слишком большой. Можно прикрепить до 2 МБ.' : ''

export const supportsFileUpload = (source: string, accountRef: string) =>
  (source === 'max' && /^max(?::.*)?$/.test(accountRef)) || (source === 'vk' && /^vk:\d+$/.test(accountRef))
