import { useEffect, useState } from "react"
import { ArrowDown, ArrowUp, Plus, Trash2, X } from "lucide-react"
import { Button } from "@/components/ui/button"

export type AutoReplyChat = { id: string; source: string; sender: string; display_name?: string; account_ref?: string }
type Rule = { id: string; conversation_id: string; match_type: "keyword" | "regex"; pattern: string; flags: string; reply_text: string; enabled: boolean; chatgpt_opt_in: boolean; position: number }
type Draft = Omit<Rule, "id" | "position">
type Preview = { matched: boolean; reply_text: string | null }
const initial = (): Draft => ({ conversation_id: "", match_type: "keyword", pattern: "", flags: "i", reply_text: "", enabled: false, chatgpt_opt_in: false })

async function request<T>(path: string, payload?: object): Promise<T> {
  const response = await fetch(path, { method: payload ? "POST" : "GET", headers: { "Content-Type": "application/json" }, ...(payload ? { body: JSON.stringify(payload) } : {}) })
  const data = await response.json().catch(() => ({})) as T & { error?: string }
  if (!response.ok) throw new Error(data.error || "Не удалось выполнить запрос")
  return data
}

export function AutoReplySettingsDialog({ open, onClose, chats }: { open: boolean; onClose: () => void; chats: AutoReplyChat[] }) {
  const [rules, setRules] = useState<Rule[]>([])
  const [draft, setDraft] = useState<Draft>(initial)
  const [editing, setEditing] = useState<string | null>(null)
  const [sample, setSample] = useState("")
  const [preview, setPreview] = useState<Preview | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const refresh = async () => setRules((await request<{ rules: Rule[] }>("/v1/auto-replies")).rules)
  useEffect(() => {
    if (!open) return
    let live = true
    request<{ rules: Rule[] }>("/v1/auto-replies")
      .then(result => { if (live) setRules(result.rules) })
      .catch(err => { if (live) setError(String(err)) })
      .finally(() => { if (live) setLoading(false) })
    return () => { live = false }
  }, [open])
  if (!open) return null

  const eligible = chats.filter(chat => Boolean(chat.account_ref) && chat.source !== "diagnostic")
  const label = (id: string) => {
    const chat = chats.find(item => item.id === id)
    return chat ? (chat.display_name || chat.sender || chat.id) + " · " + chat.source : id
  }
  const reset = () => { setDraft(initial()); setEditing(null); setSample(""); setPreview(null) }
  const startEdit = (rule: Rule) => {
    setDraft({ conversation_id: rule.conversation_id, match_type: rule.match_type, pattern: rule.pattern, flags: rule.flags, reply_text: rule.reply_text, enabled: rule.enabled, chatgpt_opt_in: rule.chatgpt_opt_in })
    setEditing(rule.id); setPreview(null); setError(""); setSuccess("")
  }
  const run = async (payload: object, onDone?: () => void) => {
    setBusy(true); setError(""); setSuccess("")
    try { await request("/v1/auto-replies", payload); await refresh(); onDone?.(); setSuccess("Сохранено") }
    catch (err) { setError(err instanceof Error ? err.message : "Неизвестная ошибка") }
    finally { setBusy(false) }
  }
  const save = () => {
    if (!draft.conversation_id) { setError("Выберите чат"); return }
    void run({ action: editing ? "update" : "create", ...(editing ? { id: editing } : {}), ...draft }, reset)
  }
  const test = async () => {
    if (!draft.conversation_id) { setError("Выберите чат"); return }
    setBusy(true); setError(""); setPreview(null)
    try {
      setPreview(await request<Preview>("/v1/auto-replies/preview", {
        conversation_id: draft.conversation_id, sample_text: sample, rule: { ...draft, enabled: true },
      }))
    } catch (err) { setError(err instanceof Error ? err.message : "Ошибка проверки") }
    finally { setBusy(false) }
  }
  const flag = (name: string) => {
    setDraft(current => ({ ...current, flags: "ims".split("").filter(item => item === name ? !current.flags.includes(item) : current.flags.includes(item)).join("") }))
    setPreview(null)
  }
  const field = "min-h-10 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm"
  return <div className="fixed inset-0 z-50 overflow-y-auto bg-background/95 p-3 md:p-8" role="dialog" aria-modal="true" aria-label="Автоответы">
    <div className="mx-auto max-w-3xl space-y-5 rounded-xl border bg-background p-4 shadow-xl md:p-6">
      <header className="flex items-center justify-between gap-3">
        <div><h2 className="text-lg font-semibold">Автоответы</h2><p className="text-sm text-muted-foreground">Первое включённое совпавшее правило отвечает один раз на новое сообщение.</p></div>
        <Button variant="outline" size="icon" onClick={onClose} aria-label="Закрыть автоответы"><X /></Button>
      </header>
      <p className="rounded-lg border p-3 text-xs text-muted-foreground">Для ChatGPT требуется отдельное включение в правиле. Обязательные подтверждения безопасности остаются за человеком. Пока нет подтверждённой классификации обычных событий ChatGPT, отправка по таким правилам не выполняется. Предпросмотр ничего не отправляет.</p>
      {loading ? <p role="status">Загружаю правила…</p> : <section aria-label="Сохранённые правила" className="space-y-2">
        {rules.length === 0 && <p className="text-sm text-muted-foreground">Правил пока нет.</p>}
        {rules.map((rule, index) => <div key={rule.id} className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
          <div className="min-w-40 flex-1">
            <p className="break-words text-sm font-medium">{label(rule.conversation_id)}</p>
            <p className="break-all text-xs text-muted-foreground">{rule.match_type === "regex" ? "Regex" : "Ключевое слово"} · {rule.pattern}</p>
            <p className="line-clamp-2 break-words text-xs">{rule.reply_text}</p>
          </div>
          <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={rule.enabled} disabled={busy} aria-label="Включить правило" onChange={e => void run({ action: "toggle", id: rule.id, enabled: e.target.checked })} />Вкл.</label>
          <Button variant="outline" size="sm" disabled={busy || index === 0} onClick={() => void run({ action: "move", id: rule.id, direction: "up" })} aria-label="Поднять правило"><ArrowUp className="size-4" /></Button>
          <Button variant="outline" size="sm" disabled={busy || index === rules.length - 1} onClick={() => void run({ action: "move", id: rule.id, direction: "down" })} aria-label="Опустить правило"><ArrowDown className="size-4" /></Button>
          <Button variant="outline" size="sm" disabled={busy} onClick={() => startEdit(rule)}>Изменить</Button>
          <Button variant="outline" size="sm" disabled={busy} aria-label="Удалить правило" onClick={() => {
            if (window.confirm("Удалить выбранное правило?"))
              void run({ action: "delete", id: rule.id }, () => { if (editing === rule.id) reset() })
          }}><Trash2 className="size-4" /></Button>
        </div>)}
      </section>}
      <form className="space-y-3 border-t pt-4" onSubmit={e => { e.preventDefault(); save() }}>
        <h3 className="font-semibold">{editing ? "Изменение правила" : "Новое правило"}</h3>
        <label className="block space-y-1 text-sm">Чат
          <select className={field} value={draft.conversation_id} onChange={e => { setDraft(cur => ({ ...cur, conversation_id: e.target.value })); setPreview(null) }}>
            <option value="">Выберите чат</option>
            {editing && draft.conversation_id && !eligible.some(chat => chat.id === draft.conversation_id) && <option value={draft.conversation_id}>{label(draft.conversation_id)}</option>}
            {eligible.map(chat => <option key={chat.id} value={chat.id}>{label(chat.id)}</option>)}
          </select>
        </label>
        <label className="block space-y-1 text-sm">Условие
          <select className={field} value={draft.match_type} onChange={e => { setDraft(cur => ({ ...cur, match_type: e.target.value as Draft["match_type"] })); setPreview(null) }}>
            <option value="keyword">Ключевое слово или фраза</option><option value="regex">Регулярное выражение</option>
          </select>
        </label>
        <label className="block space-y-1 text-sm">{draft.match_type === "regex" ? "Regex" : "Ключевое слово"}
          <input className={field} maxLength={256} required value={draft.pattern} onChange={e => { setDraft(cur => ({ ...cur, pattern: e.target.value })); setPreview(null) }} placeholder="Шаблон для совпадения" />
        </label>
        <fieldset className="flex flex-wrap gap-4 text-sm"><legend className="mb-1">Флаги</legend>
          {(["i", "m", "s"] as const).map(name => <label key={name} className="flex items-center gap-1">
            <input type="checkbox" checked={draft.flags.includes(name)} disabled={draft.match_type === "keyword" && name !== "i"} onChange={() => flag(name)} />
            {name === "i" ? "Без учёта регистра" : name === "m" ? "Многострочный" : "Точка включает перенос"}
          </label>)}
        </fieldset>
        <label className="block space-y-1 text-sm">Текст ответа
          <textarea className={field} rows={3} maxLength={2000} required value={draft.reply_text} onChange={e => setDraft(cur => ({ ...cur, reply_text: e.target.value }))} />
        </label>
        {eligible.find(chat => chat.id === draft.conversation_id)?.source.startsWith("chatgpt") && (
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.chatgpt_opt_in}
            onChange={e => setDraft(cur => ({ ...cur, chatgpt_opt_in: e.target.checked }))} />
            Разрешить это правило для обычных событий ChatGPT (opt-in)
          </label>
        )}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={draft.enabled} onChange={e => setDraft(cur => ({ ...cur, enabled: e.target.checked }))} />Правило включено</label>
        <div className="space-y-2 rounded-lg border p-3">
          <label className="block space-y-1 text-sm">Проверка на примере (без отправки)
            <textarea className={field} rows={2} maxLength={4096} value={sample} onChange={e => { setSample(e.target.value); setPreview(null) }} />
          </label>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void test()}>Проверить</Button>
          {preview && <p role="status" className="text-sm">{preview.matched ? "Совпадение. Ответ: " + preview.reply_text : "Совпадений нет"}</p>}
        </div>
        {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
        {success && <p className="text-sm" role="status">{success}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={busy} type="submit">{editing ? "Сохранить изменения" : <><Plus className="mr-1 size-4" />Добавить правило</>}</Button>
          {editing && <Button type="button" variant="outline" onClick={reset}>Отменить изменение</Button>}
        </div>
      </form>
    </div>
  </div>
}
