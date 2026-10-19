export const MAX_MCP_MEDIA_BYTES = 4 * 1024 * 1024

export function boundedMediaLimit(value) {
  if (value === undefined || value === null) return null
  if (!Number.isSafeInteger(value) || value < 1 || value > MAX_MCP_MEDIA_BYTES) {
    throw new Error("invalid media download limit")
  }
  return value
}

export function assertDeclaredMediaWithinLimit(message, maximum) {
  if (maximum === null) return
  const raw = message?.media?.document?.size
  if (raw === undefined || raw === null) return
  const size = Number(raw)
  if (Number.isFinite(size) && size > maximum) {
    throw new Error("Telegram media exceeds the bounded download limit")
  }
}
