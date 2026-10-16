// Opt-in semantic search over UserIO's own retained history.
//
// The panel owns nothing but its own settings: the server keeps the keys, and a
// missing or unreachable vector service leaves plain text search working. Index
// readiness is shown honestly, including a partial scope, so a half-indexed
// history is never presented as a complete one.
import { useCallback, useEffect, useState } from "react"

export type SemanticStatus = {
  text?: { messages?: number; conversations?: number }
  semantic?: {
    enabled?: boolean
    available?: boolean
    reason?: string
    collection?: string
    model?: string
    dimensions?: number
    points?: number
    live_messages?: number
    indexed_messages?: number
    indexed_chunks?: number
    pending?: number
    errors?: number
    partial?: number
    coverage_ratio?: number
    complete?: boolean
  }
  scope?: {
    conversation_ids?: string[]
    live_messages?: number
    indexed_messages?: number
    indexed_chunks?: number
    coverage_ratio?: number
    complete?: boolean
  }
  text_only?: boolean
}

export type SemanticSettings = {
  enabled: boolean
  qdrant_url: string
  qdrant_collection: string
  embedding_url: string
  embedding_model: string
  dimensions: number
  priority_conversations: string[]
  has_qdrant_key: boolean
  has_embedding_key: boolean
}

export type SemanticMessageHit = {
  conversation_id: string
  message_id: string
  source: string
  channel: string
  account_ref: string
  peer_id: string
  sender: string
  display_name: string
  direction: string
  author: { id: string; name: string; role: string }
  received_at?: number
  snippet: string
  match_type: string
  score: number
  deep_link: string
  provider_link?: string
}

const percent = (value?: number) => `${Math.round((value ?? 0) * 100)}%`

const scopeLine = (status: SemanticStatus | null) => {
  const scope = status?.scope
  if (!scope || !scope.conversation_ids?.length) return null
  return `Рабочие чаты: ${scope.indexed_messages ?? 0} из ${scope.live_messages ?? 0} сообщений (${percent(scope.coverage_ratio)})`
}

export function useSemanticSearch() {
  const [settings, setSettings] = useState<SemanticSettings | null>(null)
  const [status, setStatus] = useState<SemanticStatus | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [saved, coverage] = await Promise.all([
        fetch("/v1/search/settings").then((r) => r.json()),
        fetch("/v1/search/index").then((r) => r.json()),
      ])
      setSettings(saved)
      setStatus(coverage)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const save = useCallback(async (values: Partial<SemanticSettings> & { qdrant_token?: string; embedding_token?: string }) => {
    setSaving(true)
    try {
      const response = await fetch("/v1/search/settings", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values),
      })
      const payload = await response.json()
      if (!response.ok) throw new Error(payload?.error || "Не удалось сохранить настройки")
      setSettings(payload)
      await refresh()
      return payload
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
      throw reason
    } finally {
      setSaving(false)
    }
  }, [refresh])

  const state = (() => {
    if (!settings) return "loading"
    if (!settings.enabled) return "off"
    if (status?.semantic?.available) return status.semantic.complete ? "ready" : "indexing"
    return status?.semantic?.reason === "not_configured" ? "unconfigured" : "degraded"
  })()

  return { settings, status, state, saving, error, save, refresh }
}

export function SemanticSearchPanel({ session }: { session: ReturnType<typeof useSemanticSearch> }) {
  const { settings, status, state, saving, error, save } = session
  const [qdrantKey, setQdrantKey] = useState("")
  const [embeddingKey, setEmbeddingKey] = useState("")
  const [form, setForm] = useState<SemanticSettings | null>(settings)
  useEffect(() => { setForm(settings) }, [settings])

  if (!form) return <p className="text-sm text-muted-foreground">Загрузка настроек поиска…</p>

  const submit = async () => {
    await save({
      enabled: form.enabled, qdrant_url: form.qdrant_url, qdrant_collection: form.qdrant_collection,
      embedding_url: form.embedding_url, embedding_model: form.embedding_model,
      dimensions: Number(form.dimensions) || 2048,
      priority_conversations: form.priority_conversations,
      ...(qdrantKey ? { qdrant_token: qdrantKey } : {}),
      ...(embeddingKey ? { embedding_token: embeddingKey } : {}),
    })
    setQdrantKey(""); setEmbeddingKey("")
  }

  return (
    <section className="space-y-3" aria-label="Смысловой поиск по истории">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={form.enabled}
          onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />
        Искать по смыслу (Qdrant + embeddings)
      </label>
      {form.enabled && <>
        <label className="block text-xs text-muted-foreground">Адрес Qdrant
          <input className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" value={form.qdrant_url}
            placeholder="https://qdrant.example" onChange={(event) => setForm({ ...form, qdrant_url: event.target.value })} />
        </label>
        <label className="block text-xs text-muted-foreground">Коллекция
          <input className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" value={form.qdrant_collection}
            onChange={(event) => setForm({ ...form, qdrant_collection: event.target.value })} />
        </label>
        <label className="block text-xs text-muted-foreground">Ключ Qdrant {form.has_qdrant_key && <span className="text-[#2f80ed]">(сохранён)</span>}
          <input type="password" className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" value={qdrantKey}
            autoComplete="off" onChange={(event) => setQdrantKey(event.target.value)} />
        </label>
        <label className="block text-xs text-muted-foreground">Адрес embeddings
          <input className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" value={form.embedding_url}
            onChange={(event) => setForm({ ...form, embedding_url: event.target.value })} />
        </label>
        <label className="block text-xs text-muted-foreground">Модель embeddings
          <input className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" value={form.embedding_model}
            onChange={(event) => setForm({ ...form, embedding_model: event.target.value })} />
        </label>
        <label className="block text-xs text-muted-foreground">Размерность вектора
          <input className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" type="number" min={16} max={8192}
            value={form.dimensions} onChange={(event) => setForm({ ...form, dimensions: Number(event.target.value) || 2048 })} />
          <span className="mt-1 block">Должна совпадать с моделью: 2048 для Qwen3 Embedding 4B, 1536 для многих других.</span>
        </label>
        <label className="block text-xs text-muted-foreground">Ключ embeddings {form.has_embedding_key && <span className="text-[#2f80ed]">(сохранён)</span>}
          <input type="password" className="mt-1 w-full rounded-lg border px-2 py-1.5 text-sm" value={embeddingKey}
            autoComplete="off" onChange={(event) => setEmbeddingKey(event.target.value)} />
        </label>
      </>}
      <div className="flex items-center gap-2">
        <button type="button" className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-50"
          disabled={saving} onClick={() => { void submit() }}>{saving ? "Сохраняю…" : "Сохранить"}</button>
        {state === "ready" && <span className="text-xs text-[#2f80ed]">Индекс готов</span>}
        {state === "indexing" && <span className="text-xs text-muted-foreground">Индексация идёт</span>}
        {state === "unconfigured" && <span className="text-xs text-muted-foreground">Укажите Qdrant и embeddings</span>}
        {state === "degraded" && <span className="text-xs text-amber-600">Векторный поиск недоступен ({status?.semantic?.reason || "проверьте настройки"}), работает текстовый</span>}
        {status?.semantic?.reason === "collection_missing" && <span className="text-xs text-amber-600">Коллекция не создана: создайте {status.semantic.collection} или выдайте ключу право create</span>}
        {state === "off" && <span className="text-xs text-muted-foreground">Смысловой поиск выключен</span>}
      </div>
      {status?.semantic?.enabled && <p className="text-xs text-muted-foreground">
        Проиндексировано {status.semantic.indexed_messages ?? 0} из {status.semantic.live_messages ?? 0}
        сообщений ({status.semantic.indexed_chunks ?? 0} чанков{status.semantic.pending ? `, в очереди ${status.semantic.pending}` : ""})
        {status.semantic.errors ? `, ошибок ${status.semantic.errors}` : ""}
        {status.semantic.partial ? `, частично ${status.semantic.partial}` : ""}
      </p>}
      {scopeLine(status) && <p className="text-xs text-muted-foreground">{scopeLine(status)}</p>}
      {status?.text_only && <p className="text-xs text-muted-foreground">
        Текстовый поиск работает по {status.text?.messages ?? 0} сообщениям в {status.text?.conversations ?? 0} чатах.
      </p>}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </section>
  )
}

// Deep links are plain hash routes, so a search hit opens the exact message.
export function parseDeepLink(hash: string): { conversationId: string; messageId: string } | null {
  const raw = hash.replace(/^#\/?/, "")
  if (!raw.startsWith("chat/")) return null
  const [path, query] = raw.slice("chat/".length).split("?")
  const conversationId = decodeURIComponent(path || "")
  if (!conversationId) return null
  return { conversationId, messageId: new URLSearchParams(query || "").get("m") || "" }
}

export const hitAuthorLabel = (hit: SemanticMessageHit) =>
  hit.author?.role === "owner" ? "Вы" : (hit.author?.name || hit.display_name || hit.sender || "")

export const hitChannelLabel = (hit: SemanticMessageHit) =>
  hit.match_type === "semantic" ? "по смыслу" : hit.match_type === "fuzzy" ? "похоже" : "текст"