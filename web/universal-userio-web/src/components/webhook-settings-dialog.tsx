import { useCallback, useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

type Settings = {
  configured: boolean
  enabled: boolean
  endpoint: string
  include_message_text: boolean
  secret_configured: boolean
  last_attempt_at: number | null
  last_success_at: number | null
  last_error: string
  one_time_signing_secret?: string
}

const empty: Settings = {
  configured: false, enabled: false, endpoint: "", include_message_text: false,
  secret_configured: false, last_attempt_at: null, last_success_at: null, last_error: "",
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      credentials: "same-origin", ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
    })
  } catch (reason) {
    if (reason instanceof TypeError) {
      throw new Error("Не удалось связаться с UserIO. Проверьте соединение и повторите.")
    }
    throw reason
  }
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`)
  return payload as T
}

export function WebhookSettingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [settings, setSettings] = useState<Settings>(empty)
  const [secret, setSecret] = useState("")
  const [loading, setLoading] = useState(false)
  const [settingsLoaded, setSettingsLoaded] = useState(false)
  const [loadFailed, setLoadFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [rotateSecret, setRotateSecret] = useState(false)
  const [requestCopied, setRequestCopied] = useState(false)
  const [requestCopyError, setRequestCopyError] = useState(false)

  const loadSettings = useCallback(async () => {
    setLoading(true); setLoadFailed(false); setSettingsLoaded(false)
    setError(""); setNotice(""); setSecret(""); setRotateSecret(false); setRequestCopied(false); setRequestCopyError(false)
    try {
      const value = await request<Settings>("/v1/webhooks/incoming")
      setSettings({ ...empty, ...value })
      setSettingsLoaded(true)
    } catch (reason) {
      setLoadFailed(true)
      setError((reason as Error).message)
    } finally { setLoading(false) }
  }, [])

  useEffect(() => {
    if (open) void loadSettings()
  }, [open, loadSettings])

  if (!open) return null

  const save = async () => {
    setSaving(true); setError(""); setNotice("")
    try {
      const value = await request<Settings>("/v1/webhooks/incoming", {
        method: "POST",
        body: JSON.stringify({
          endpoint: settings.endpoint, enabled: settings.enabled,
          include_message_text: settings.include_message_text, rotate_secret: rotateSecret,
        }),
      })
      setSettings(current => ({ ...current, ...value }))
      setRotateSecret(false)
      if (value.one_time_signing_secret) {
        setSecret(value.one_time_signing_secret)
        setNotice("Скопируйте ключ подписи сейчас и добавьте его на стороне получателя. Позже UserIO его не покажет.")
      } else setNotice("Настройки сохранены. Старые входящие сообщения не отправляются.")
    } catch (reason) { setError((reason as Error).message) }
    finally { setSaving(false) }
  }

  const test = async () => {
    setTesting(true); setError(""); setNotice("")
    try {
      await request("/v1/webhooks/incoming/test", { method: "POST", body: "{}" })
      setNotice("Проверочное событие доставлено. Сообщения из переписок не отправлялись.")
    } catch (reason) { setError((reason as Error).message) }
    finally { setTesting(false) }
  }

  return <div className="fixed inset-0 z-[70] grid place-items-start bg-black/60 p-2 sm:place-items-center sm:p-4" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section role="dialog" aria-modal="true" aria-labelledby="webhook-title" className="flex max-h-[calc(100svh-1rem)] min-h-0 w-full max-w-lg flex-col overflow-hidden rounded-2xl border bg-background p-4 shadow-2xl sm:max-h-[90svh] sm:p-5">
      <header className="shrink-0"><h2 id="webhook-title" className="text-lg font-semibold">Передача новых сообщений</h2><p className="mt-1 text-sm text-muted-foreground">Новые входящие можно передавать в Agent Herder или другой сервис.</p></header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain touch-pan-y">
      {loading ? <p className="py-8 text-sm text-muted-foreground">Загружаю настройки…</p> : loadFailed ? <div className="mt-4 space-y-3 rounded-xl border border-destructive/40 p-4">
        <p className="text-sm font-medium">Не удалось загрузить настройки</p>
        <p role="alert" className="text-sm text-destructive">{error}</p>
        <p className="text-sm text-muted-foreground">Настройки не изменены. Проверьте соединение и попробуйте ещё раз.</p>
        <Button variant="outline" onClick={() => void loadSettings()}>Повторить</Button>
      </div> : settingsLoaded && <div className="mt-4 space-y-4">

        <details className="rounded-xl border p-3 text-sm">
          <summary className="cursor-pointer font-medium">Как подключить</summary>
          <div className="mt-3 space-y-2 text-sm text-muted-foreground">
            <p>Нужен готовый публичный HTTPS-адрес приёмника. MCP-подключение само сообщения не принимает.</p>
            <ol className="list-decimal space-y-1 pl-5">
              <li>Попросите администратора настроить приём сообщений и выдать адрес.</li>
              <li>Сохраните адрес. UserIO покажет ключ подписи один раз.</li>
              <li>Передайте ключ администратору безопасным способом. Включите отправку после подтверждения, что ключ добавлен.</li>
            </ol>
            <p>Отправляются только новые входящие из разрешённых чатов. Текст сообщений по умолчанию не передаётся.</p>
            <details className="rounded-lg border bg-background/50 p-3">
              <summary className="cursor-pointer font-medium">Текст для администратора</summary>
              <p className="mt-2">«Настрой в GPTAdmin приём новых сообщений UserIO и передачу в нужную сессию Agent Herder. Пришли HTTPS-адрес и объясни, как безопасно передать ключ. Не включай отправку, пока ключ не добавлен».</p>
              <Button className="mt-2" size="sm" variant="outline" onClick={async () => {
                try {
                  await navigator.clipboard.writeText("Настрой в GPTAdmin приём новых сообщений UserIO и передачу в нужную сессию Agent Herder. Пришли HTTPS-адрес и объясни, как безопасно передать ключ. Не включай отправку, пока ключ не добавлен.")
                  setRequestCopied(true)
                  setRequestCopyError(false)
                } catch {
                  setRequestCopied(false)
                  setRequestCopyError(true)
                }
              }}>{requestCopied ? "Скопировано" : "Скопировать текст"}</Button>
              {requestCopyError && <p role="status" className="mt-1 text-xs">Не удалось скопировать. Выделите текст выше и скопируйте вручную.</p>}
            </details>
          </div>
        </details>
        <details className="rounded-xl border p-3 text-sm">
          <summary className="cursor-pointer font-medium">Для разработчика</summary>
          <div className="mt-3 space-y-2 text-xs text-muted-foreground">
            <p>UserIO отправляет POST с JSON и заголовками:</p>
            <code className="block break-all rounded-md bg-muted p-2">X-UserIO-Event · X-UserIO-Timestamp · X-UserIO-Signature · Idempotency-Key</code>
            <p>Подпись — HMAC-SHA256 от <code>timestamp + "." + исходное тело запроса</code>; заголовок содержит <code>sha256=&lt;hex&gt;</code>. Timestamp — Unix-время в секундах. Приёмник должен отклонять слишком старые запросы.</p>
            <p>UserIO повторяет доставку при ошибке, таймауте или ответе не 2xx. Интервал растёт от 5 секунд до 30 минут; общего лимита попыток нет. При повторах подпись и время обновляются, <code>Idempotency-Key</code> остаётся прежним. Приёмник должен пропускать дубликаты и отвечать 2xx.</p>
            <p>Событие: <code>message.received</code>, схема <code>userio.inbound-message.v1</code>.</p>
            <details className="rounded-lg border bg-background/50 p-3">
              <summary className="cursor-pointer font-medium">Пример JSON</summary>
              <p className="mt-2">Текст по умолчанию не передаётся. При включённой передаче добавляются поля <code>text</code> и <code>text_truncated</code>; текст ограничен 8000 символами.</p>
              <pre className="mt-2 overflow-x-auto rounded-md bg-muted p-2 text-[11px] leading-relaxed">{`{
  "schema": "userio.inbound-message.v1",
  "event_id": "<стабильный-id>",
  "event": {
    "seq": 42,
    "source": "telegram",
    "account_ref": "telegram:123",
    "message_id": "<id-сообщения>",
    "conversation_id": "<id-чата>",
    "sender": "Алиса",
    "received_at": 1792287375.2,
    "sender_is_bot": false
  }
}`}</pre>
            </details>
            <p>Кнопка проверки отправляет событие <code>webhook.test</code> без данных переписки. Ответ HTTP 2xx подтверждает доставку, но не обработку события в Agent Herder.</p>
          </div>
        </details>
        <label className="block space-y-1 text-sm"><span>HTTPS-адрес получателя</span><Input title="Публичный адрес приёмника, который настроил администратор" type="url" inputMode="url" autoComplete="url" placeholder="https://…" value={settings.endpoint} onChange={event => setSettings(current => ({ ...current, endpoint: event.target.value }))} /></label>

        <label title="События начнут отправляться после сохранения. Пока передача выключена, новые события не сохраняются для последующей отправки." className="flex items-start gap-3 rounded-xl border p-3 text-sm"><input type="checkbox" className="mt-1" checked={settings.enabled} onChange={event => setSettings(current => ({ ...current, enabled: event.target.checked }))} /><span><span className="block font-medium">Отправлять новые сообщения</span><span className="text-xs text-muted-foreground">Начать передачу после сохранения</span></span></label>
        <label title="Когда переключатель выключен, получатель получает данные о сообщении без текста. Сам текст можно прочитать в UserIO при наличии доступа." className="flex items-start gap-3 rounded-xl border p-3 text-sm"><input type="checkbox" className="mt-1" checked={settings.include_message_text} onChange={event => setSettings(current => ({ ...current, include_message_text: event.target.checked }))} /><span><span className="block font-medium">Передавать текст сообщения</span><span className="text-xs text-muted-foreground">По умолчанию текст не передаётся</span></span></label>
        {settings.secret_configured && <label title="Заменит текущий ключ после сохранения. Новый ключ нужно добавить у получателя." className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1" checked={rotateSecret} onChange={event => setRotateSecret(event.target.checked)} /><span><span className="block font-medium">Перевыпустить ключ подписи</span></span></label>}
        {settings.last_error && <p role="status" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">Последняя доставка не удалась: {settings.last_error}. UserIO повторит её автоматически.</p>}
        {secret && <div className="space-y-2 rounded-xl border border-primary/40 bg-muted p-3"><p className="text-sm font-medium">Ключ подписи — сохраните его у получателя</p><code className="block break-all text-xs">{secret}</code><Button size="sm" variant="outline" onClick={() => void navigator.clipboard.writeText(secret)}>Скопировать ключ</Button></div>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
      </div>}
      </div>
      {settingsLoaded && <footer className="flex shrink-0 flex-wrap justify-end gap-2 border-t bg-background pt-3"><Button variant="outline" onClick={onClose}>Закрыть</Button><span className="flex flex-col items-end gap-1"><Button variant="outline" disabled={loading || !settings.configured || testing || saving} onClick={() => void test()}>{testing ? "Проверяю…" : "Проверить связь"}</Button>{!settings.configured && <span className="text-right text-xs text-muted-foreground">Сначала сохраните адрес получателя</span>}</span><Button disabled={loading || saving} onClick={() => void save()}>{saving ? "Сохраняю…" : "Сохранить"}</Button></footer>}
    </section>
  </div>
}
