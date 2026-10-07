export type ContextModels = {
  text?: string
  image?: string
}

export type ContextSettings = {
  message_count: number
  max_message_count: number
  token_budget: number
  max_token_budget: number
  summary_token_budget: number
  summary_retention_days: number
  cache_channels: Record<string, boolean>
  cache_conversation_kinds: Record<string, boolean>
  models?: ContextModels
} & Record<string, unknown>

export type ContextSettingsSession = {
  confirmed: ContextSettings | null
  draft: ContextSettings
  status: "idle" | "loading" | "ready" | "error"
  loadError: string | null
}

export const DEFAULT_CONTEXT_SETTINGS: ContextSettings = {
  message_count: 3,
  max_message_count: 150,
  token_budget: 1000,
  max_token_budget: 48000,
  summary_token_budget: 1200,
  summary_retention_days: 90,
  cache_channels: {
    telegram: true,
    gmail: false,
    email: false,
    whatsapp: false,
    max: false,
    sms: false,
    chatgpt: false,
    vk: false,
  },
  cache_conversation_kinds: {
    direct: true,
    group: false,
    channel: false,
  },
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}

const finiteNumber = (value: unknown, fallback: number): number => {
  if (value === null || value === undefined || value === "") return fallback
  const parsed = typeof value === "number" ? value : Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback
}

const booleanMap = (value: unknown, fallback: Record<string, boolean>) => {
  const incoming = record(value)
  const normalized: Record<string, boolean> = { ...fallback }
  for (const [key, enabled] of Object.entries(incoming)) {
    if (typeof enabled === "boolean") normalized[key] = enabled
  }
  return normalized
}

/**
 * Normalizes both the legacy two-field response and the extended context
 * policy. Unknown keys are deliberately retained so a newer backend can roll
 * out before this client without losing its settings on the next save.
 */
export const normalizeContextSettings = (value: unknown): ContextSettings => {
  const source = record(value)
  const modelSource = record(source.models)
  const textModel = typeof modelSource.text === "string" ? modelSource.text : undefined
  const imageModel = typeof modelSource.image === "string" ? modelSource.image : undefined
  const modelsExposed = typeof modelSource.text === "string" || typeof modelSource.image === "string"
  const channelSource = record(source.cache_channels)
  const cacheChannels = booleanMap(channelSource, DEFAULT_CONTEXT_SETTINGS.cache_channels)
  if (typeof channelSource.gmail !== "boolean" && typeof channelSource.email === "boolean") {
    cacheChannels.gmail = channelSource.email
  }
  cacheChannels.email = cacheChannels.gmail

  const normalized: ContextSettings = {
    ...source,
    message_count: finiteNumber(source.message_count, DEFAULT_CONTEXT_SETTINGS.message_count),
    max_message_count: finiteNumber(source.max_message_count, DEFAULT_CONTEXT_SETTINGS.max_message_count),
    token_budget: finiteNumber(source.token_budget, DEFAULT_CONTEXT_SETTINGS.token_budget),
    max_token_budget: finiteNumber(source.max_token_budget, DEFAULT_CONTEXT_SETTINGS.max_token_budget),
    summary_token_budget: finiteNumber(source.summary_token_budget, DEFAULT_CONTEXT_SETTINGS.summary_token_budget),
    summary_retention_days: finiteNumber(source.summary_retention_days, DEFAULT_CONTEXT_SETTINGS.summary_retention_days),
    cache_channels: cacheChannels,
    cache_conversation_kinds: booleanMap(source.cache_conversation_kinds, DEFAULT_CONTEXT_SETTINGS.cache_conversation_kinds),
  }
  if (modelsExposed) normalized.models = { text: textModel, image: imageModel }
  else delete normalized.models
  return normalized
}

export const contextSettingsPayload = (settings: ContextSettings): ContextSettings =>
  normalizeContextSettings(settings)

export const createContextSettingsSession = (): ContextSettingsSession => ({
  confirmed: null,
  draft: normalizeContextSettings(DEFAULT_CONTEXT_SETTINGS),
  status: "idle",
  loadError: null,
})

export const beginContextSettingsLoad = (session: ContextSettingsSession): ContextSettingsSession => ({
  ...session,
  draft: session.confirmed ?? session.draft,
  status: "loading",
  loadError: null,
})

export const completeContextSettingsLoad = (session: ContextSettingsSession, value: unknown): ContextSettingsSession => {
  if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).length === 0) {
    return failContextSettingsLoad(session, "Сервер вернул некорректный ответ. Повторите загрузку.")
  }
  const confirmed = normalizeContextSettings(value)
  return { ...session, confirmed, draft: confirmed, status: "ready", loadError: null }
}

export const failContextSettingsLoad = (session: ContextSettingsSession, reason: string): ContextSettingsSession => ({
  ...session,
  draft: session.confirmed ?? session.draft,
  status: "error",
  loadError: `Не удалось загрузить сохранённые настройки. ${reason}`,
})

export const confirmContextSettingsSave = (session: ContextSettingsSession, value: unknown): ContextSettingsSession => {
  const confirmed = normalizeContextSettings(value)
  return { ...session, confirmed, draft: confirmed, status: "ready", loadError: null }
}

export const contextSettingsValidationError = (settings: ContextSettings): string | null => {
  const ranges: Array<[keyof ContextSettings, string, number, number]> = [
    ["message_count", "Минимум сообщений", 0, 20],
    ["max_message_count", "Максимум сообщений", 1, 1000],
    ["token_budget", "Стартовые токены", 0, 8000],
    ["max_token_budget", "Максимум токенов", 64, 100000],
    ["summary_token_budget", "Размер саммари", 64, 4096],
    ["summary_retention_days", "Срок хранения", 0, 3650],
  ]
  for (const [key, label, minimum, maximum] of ranges) {
    const value = settings[key]
    if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) {
      return `${label}: введите целое число от ${minimum} до ${maximum}`
    }
  }
  if (settings.max_message_count < settings.message_count) {
    return "Максимум сообщений не может быть меньше стартового окна"
  }
  if (settings.max_token_budget < settings.token_budget) {
    return "Максимум токенов не может быть меньше стартового бюджета"
  }
  return null
}
