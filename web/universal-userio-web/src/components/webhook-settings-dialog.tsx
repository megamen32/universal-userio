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

  const loadSettings = useCallback(async () => {
    setLoading(true); setLoadFailed(false); setSettingsLoaded(false)
    setError(""); setNotice(""); setSecret(""); setRotateSecret(false)
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

  return <div className="fixed inset-0 z-[70] grid place-items-center bg-black/60 p-4" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <section role="dialog" aria-modal="true" aria-labelledby="webhook-title" className="max-h-[90svh] w-full max-w-lg overflow-y-auto rounded-2xl border bg-background p-5 shadow-2xl">
      <header><h2 id="webhook-title" className="text-lg font-semibold">Передача новых сообщений</h2><p className="mt-1 text-sm text-muted-foreground">Это настройка отправки новых входящих сообщений в другую систему, а не всплывающие уведомления браузера.</p></header>
      {loading ? <p className="py-8 text-sm text-muted-foreground">Загружаю настройки…</p> : loadFailed ? <div className="mt-4 space-y-3 rounded-xl border border-destructive/40 p-4">
        <p className="text-sm font-medium">Не удалось загрузить настройки</p>
        <p role="alert" className="text-sm text-destructive">{error}</p>
        <p className="text-sm text-muted-foreground">Настройки не изменены. Проверьте соединение и попробуйте ещё раз.</p>
        <Button variant="outline" onClick={() => void loadSettings()}>Повторить</Button>
      </div> : settingsLoaded && <div className="mt-4 space-y-4">
        <div className="rounded-xl bg-muted/50 p-3 text-sm">
          <p className="font-medium">Сначала подготовьте получателя</p>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
            <li>Попросите владельца нужной системы подготовить HTTPS-адрес для новых сообщений из UserIO.</li>
            <li>Он также должен сказать, куда добавить ключ безопасности: UserIO покажет его один раз после сохранения.</li>
          </ul>
          <p className="mt-2 text-sm"><span className="font-medium">Можно написать владельцу Agent Herder:</span> «Подготовь HTTPS-адрес, который будет принимать новые сообщения из UserIO, и подскажи, куда добавить ключ безопасности. Пришли мне этот адрес». Само MCP-подключение адресом не является: нужен отдельно настроенный веб-приёмник.</p>
        </div>
        <label className="block space-y-1 text-sm"><span>HTTPS-адрес получателя</span><Input type="url" inputMode="url" autoComplete="url" placeholder="Например, https://ваш-сервис.example/…" value={settings.endpoint} onChange={event => setSettings(current => ({ ...current, endpoint: event.target.value }))} /></label>
        <details className="rounded-xl border p-3 text-sm">
          <summary className="cursor-pointer font-medium">Техническая настройка приёмника и проверка подписи</summary>
          <div className="mt-3 space-y-2 text-xs text-muted-foreground">
            <p>Сохраните адрес: новый ключ подписи появится здесь один раз. Скопируйте его в секреты приёмника. При перевыпуске ключа обновите его и там.</p>
            <p>UserIO отправляет POST с JSON. Приёмнику нужны заголовки:</p>
            <code className="block break-all rounded-md bg-muted p-2">X-UserIO-Event · X-UserIO-Timestamp · X-UserIO-Signature · Idempotency-Key</code>
            <p>Подпись — HMAC-SHA256 от строки <code>timestamp + "." + исходное тело запроса</code>, результат передаётся как <code>sha256=&lt;hex&gt;</code>. Приёмник сам задаёт допустимое время подписи и должен отклонять слишком старые запросы. Ключ идемпотентности одинаков для повторов одного события.</p>
            <p>При недоставке UserIO повторяет событие: интервал растёт от 5 секунд до 30 минут. После ответа HTTP 2xx очередь переходит к следующему событию.</p>
            <p>Событие нового сообщения имеет тип <code>message.received</code> и схему <code>userio.inbound-message.v1</code>. Пример тела без текста переписки:</p>
            <pre className="overflow-x-auto rounded-md bg-muted p-2 text-[11px] leading-relaxed">{`{
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
            <p>Кнопка проверки отправляет отдельное событие <code>webhook.test</code> без данных переписки. Успех означает, что адрес ответил HTTP 2xx; это не подтверждает обработку события в Agent Herder.</p>
            <p>Для Agent Herder укажите его совместимый HTTPS webhook-приёмник. UserIO не вызывает MCP-инструменты напрямую.</p>
          </div>
        </details>
        <label className="flex items-start gap-3 rounded-xl border p-3 text-sm"><input type="checkbox" className="mt-1" checked={settings.enabled} onChange={event => setSettings(current => ({ ...current, enabled: event.target.checked }))} /><span><span className="block font-medium">Отправлять события</span><span className="text-xs text-muted-foreground">События начнут идти после сохранения. Пока настройка выключена, сообщения не накапливаются для будущей отправки.</span></span></label>
        <label className="flex items-start gap-3 rounded-xl border p-3 text-sm"><input type="checkbox" className="mt-1" checked={settings.include_message_text} onChange={event => setSettings(current => ({ ...current, include_message_text: event.target.checked }))} /><span><span className="block font-medium">Включать текст сообщения</span><span className="text-xs text-muted-foreground">Если выключено, получатель увидит сведения о сообщении без его текста. Получить сам текст отдельно он сможет только при наличии доступа к UserIO.</span></span></label>
        {settings.secret_configured && <label className="flex items-start gap-3 text-sm"><input type="checkbox" className="mt-1" checked={rotateSecret} onChange={event => setRotateSecret(event.target.checked)} /><span><span className="block font-medium">Перевыпустить ключ подписи</span><span className="text-xs text-muted-foreground">После сохранения заменит текущий ключ. Его нужно обновить у получателя.</span></span></label>}
        {settings.last_error && <p role="status" className="rounded-lg border border-destructive/40 p-3 text-sm text-destructive">Последняя доставка не удалась: {settings.last_error}. UserIO повторит её автоматически.</p>}
        {secret && <div className="space-y-2 rounded-xl border border-primary/40 bg-muted p-3"><p className="text-sm font-medium">Ключ подписи — сохраните его у получателя</p><code className="block break-all text-xs">{secret}</code><Button size="sm" variant="outline" onClick={() => void navigator.clipboard.writeText(secret)}>Скопировать ключ</Button></div>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
        <p className="text-xs text-muted-foreground">Отправляются только новые входящие сообщения из чатов, разрешённых в настройках обработки. Ссылка принимает только публичные HTTPS-адреса; каждый запрос содержит HMAC-подпись и ключ идемпотентности.</p>
        <footer className="flex flex-wrap justify-end gap-2 border-t pt-3"><Button variant="outline" onClick={onClose}>Закрыть</Button><span className="flex flex-col items-end gap-1"><Button variant="outline" disabled={loading || !settingsLoaded || !settings.configured || testing || saving} onClick={() => void test()}>{testing ? "Проверяю…" : "Проверить связь"}</Button>{!settings.configured && <span className="text-right text-xs text-muted-foreground">Сначала сохраните адрес получателя</span>}</span><Button disabled={loading || !settingsLoaded || saving} onClick={() => void save()}>{saving ? "Сохраняю…" : "Сохранить"}</Button></footer>
      </div>}
    </section>
  </div>
}
