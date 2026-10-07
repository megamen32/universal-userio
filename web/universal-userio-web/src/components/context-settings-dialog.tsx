import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

import { contextSettingsValidationError, type ContextSettings } from "../context-settings"

type ContextSettingsDialogProps = {
  settings: ContextSettings
  loading: boolean
  ready: boolean
  loadError: string | null
  saving: boolean
  onChange: (settings: ContextSettings) => void
  onClose: () => void
  onRetry: () => void
  onSave: () => void
}

const CHANNELS = [
  ["telegram", "Telegram", "Включено по умолчанию"],
  ["gmail", "Email / Gmail", "Выключено по умолчанию"],
  ["whatsapp", "WhatsApp", "Выключено по умолчанию"],
  ["max", "MAX", "Выключено по умолчанию"],
  ["sms", "SMS", "Выключено по умолчанию"],
  ["chatgpt", "ChatGPT", "Выключено по умолчанию"],
  ["vk", "VK", "Выключено по умолчанию"],
] as const

const CONVERSATION_KINDS = [
  ["direct", "Личные переписки", "Включено по умолчанию"],
  ["group", "Группы", "Выключено по умолчанию"],
  ["channel", "Каналы", "Выключено по умолчанию"],
] as const

export function ContextSettingsDialog({ settings, loading, ready, loadError, saving, onChange, onClose, onRetry, onSave }: ContextSettingsDialogProps) {
  const validationError = contextSettingsValidationError(settings)
  const setNumber = (key: "message_count" | "max_message_count" | "token_budget" | "max_token_budget" | "summary_token_budget" | "summary_retention_days", value: string) => {
    onChange({ ...settings, [key]: Number(value) })
  }
  const setChannel = (key: string, enabled: boolean) => {
    const cacheChannels = { ...settings.cache_channels, [key]: enabled }
    if (key === "gmail") cacheChannels.email = enabled
    onChange({ ...settings, cache_channels: cacheChannels })
  }
  const setConversationKind = (key: string, enabled: boolean) => {
    onChange({ ...settings, cache_conversation_kinds: { ...settings.cache_conversation_kinds, [key]: enabled } })
  }
  return <div role="dialog" aria-modal="true" aria-labelledby="context-settings-title" className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-3 sm:p-5" onClick={onClose}>
    <div className="max-h-[92vh] w-full max-w-3xl overflow-y-auto rounded-2xl border bg-card p-5 shadow-2xl sm:p-6" onClick={(event) => event.stopPropagation()}>
      <header>
        <h2 id="context-settings-title" className="text-lg font-semibold">Контекст и кэш ИИ</h2>
        <p className="mt-1 text-sm text-muted-foreground">Агент начинает с малого окна и сам дочитывает переписку до достаточного контекста.</p>
      </header>

      {loading && <p className="mt-4 rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">Загружаю сохранённые настройки…</p>}
      {loadError && <div role="alert" className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">
        <span>{loadError} Сохранение заблокировано, чтобы не перезаписать настройки.</span>
        <Button variant="outline" size="sm" disabled={loading} onClick={onRetry}>Повторить загрузку</Button>
      </div>}
      <p className="mt-4 rounded-lg bg-[#2f80ed]/8 px-3 py-2 text-sm text-foreground/80"><strong>По умолчанию:</strong> кэшируются личные диалоги Telegram; Email / Gmail не кэшируется.</p>

      <fieldset disabled={!ready || loading || saving} className="m-0 min-w-0 border-0 p-0">
      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <section className="rounded-xl border p-4">
          <h3 className="font-medium">Стартовое окно</h3>
          <p className="mt-1 text-xs text-muted-foreground">Минимум даётся сразу. Агент может запросить больше, но не выйдет за адаптивный потолок.</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <NumberSetting label="Минимум сообщений" hint="Первое чтение" min={0} max={20} step={1} value={settings.message_count} onChange={(value) => setNumber("message_count", value)} />
            <NumberSetting label="Максимум сообщений" hint="Адаптивный потолок" min={1} max={1000} step={1} value={settings.max_message_count} onChange={(value) => setNumber("max_message_count", value)} />
            <NumberSetting label="Стартовые токены" hint="Первый контекст" min={0} max={8000} step={100} value={settings.token_budget} onChange={(value) => setNumber("token_budget", value)} />
            <NumberSetting label="Максимум токенов" hint="Адаптивный потолок" min={64} max={100000} step={100} value={settings.max_token_budget} onChange={(value) => setNumber("max_token_budget", value)} />
          </div>
        </section>

        <section className="rounded-xl border p-4">
          <h3 className="font-medium">Суммаризация</h3>
          <p className="mt-1 text-xs text-muted-foreground">Краткая выжимка подмешивается автоматически и обновляется по мере появления сообщений.</p>
          <div className="mt-4 grid grid-cols-2 gap-3">
            <NumberSetting label="Размер саммари" hint="Токенов на диалог" min={64} max={4096} step={64} value={settings.summary_token_budget} onChange={(value) => setNumber("summary_token_budget", value)} />
            <NumberSetting label="Хранить" hint="Дней в кэше; 0 — без срока" min={0} max={3650} step={1} value={settings.summary_retention_days} onChange={(value) => setNumber("summary_retention_days", value)} />
          </div>
        </section>
      </div>

      <div className="mt-5 grid gap-5 md:grid-cols-2">
        <ChoiceSection title="Каналы" description="Для почты кэш выключен: там обычно много коротких и шумных цепочек.">
          {CHANNELS.map(([key, label, hint]) => <ToggleRow key={key} label={label} hint={hint} checked={Boolean(settings.cache_channels[key])} onChange={(enabled) => setChannel(key, enabled)} />)}
        </ChoiceSection>
        <ChoiceSection title="Типы переписок" description="Telegram-кэш ориентирован прежде всего на личные диалоги.">
          {CONVERSATION_KINDS.map(([key, label, hint]) => <ToggleRow key={key} label={label} hint={hint} checked={Boolean(settings.cache_conversation_kinds[key])} onChange={(enabled) => setConversationKind(key, enabled)} />)}
        </ChoiceSection>
      </div>

      </fieldset>
      {settings.models && ("text" in settings.models || "image" in settings.models) && <section className="mt-5 rounded-xl border p-4">
        <h3 className="font-medium">Модели разбора</h3>
          <p className="mt-1 text-xs text-muted-foreground">Текст идёт в быструю модель; безопасно загруженные изображения Telegram и WhatsApp автоматически переключают разбор на vision-модель. Модели задаются владельцем deployment.</p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {settings.models.text !== undefined && <label className="text-sm"><span className="mb-1 block text-muted-foreground">Без изображений</span><Input readOnly value={settings.models.text} placeholder="MiniMax-M2.7" /></label>}
          {settings.models.image !== undefined && <label className="text-sm"><span className="mb-1 block text-muted-foreground">С изображениями</span><Input readOnly value={settings.models.image} placeholder="MiniMax-M3" /></label>}
        </div>
      </section>}

      {validationError && <p role="alert" className="mt-4 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{validationError}</p>}
      <div className="mt-6 flex items-center justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose}>Отмена</Button>
        <Button size="sm" disabled={!ready || loading || saving || Boolean(validationError)} onClick={onSave}>{saving ? "Сохраняю…" : "Сохранить"}</Button>
      </div>
    </div>
  </div>
}

function NumberSetting({ label, hint, min, max, step, value, onChange }: { label: string; hint: string; min: number; max: number; step: number; value: number; onChange: (value: string) => void }) {
  return <label className="text-sm">
    <span className="block font-medium">{label}</span>
    <span className="mb-1 block text-[11px] text-muted-foreground">{hint}</span>
    <Input type="number" min={min} max={max} step={step} value={value} onChange={(event) => onChange(event.target.value)} />
  </label>
}

function ChoiceSection({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  return <section className="rounded-xl border p-4">
    <h3 className="font-medium">{title}</h3>
    <p className="mt-1 text-xs text-muted-foreground">{description}</p>
    <div className="mt-3 divide-y">{children}</div>
  </section>
}

function ToggleRow({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return <label className="flex cursor-pointer items-center justify-between gap-4 py-2.5 text-sm">
    <span><span className="block font-medium">{label}</span><span className="block text-[11px] text-muted-foreground">{hint}</span></span>
    <input className="size-4 accent-[#2f80ed]" type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
  </label>
}
