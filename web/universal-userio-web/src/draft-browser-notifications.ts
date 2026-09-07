type PendingDraft = {
  id: string
  conversation_id: string
  body: string
  created_at: number
  source: string
  sender: string
  identity_id?: string | null
  display_name?: string
}

type Capabilities = { subscribe: boolean }

const POLL_MS = 2500
const SEEN_KEY = "userio-browser-draft-notified-v1"
const INITIALIZED_KEY = "userio-browser-draft-notifications-initialized-v1"
const PROMPT_ID = "userio-browser-notification-prompt"
const LEASE_KEY = "userio-browser-draft-notifier-lease-v1"
const TAB_ID = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`
const LEASE_MS = 8000

const readSeen = () => {
  try { return new Set<string>(JSON.parse(localStorage.getItem(SEEN_KEY) || "[]")) }
  catch { return new Set<string>() }
}

const writeSeen = (ids: Iterable<string>) => {
  localStorage.setItem(SEEN_KEY, JSON.stringify(Array.from(ids).slice(-500)))
}

const label = (draft: PendingDraft) => draft.display_name?.trim() || draft.identity_id || draft.sender || draft.source
const preview = (text: string) => text.replace(/\s+/g, " ").trim().slice(0, 180)

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init, headers: { "Content-Type": "application/json", ...init?.headers } })
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<T>
}

function ownsNotifierLease() {
  const now = Date.now()
  try {
    const current = JSON.parse(localStorage.getItem(LEASE_KEY) || "{}") as { id?: string; until?: number }
    if (current.id && current.id !== TAB_ID && Number(current.until || 0) > now) return false
    localStorage.setItem(LEASE_KEY, JSON.stringify({ id: TAB_ID, until: now + LEASE_MS }))
    const confirmed = JSON.parse(localStorage.getItem(LEASE_KEY) || "{}") as { id?: string }
    return confirmed.id === TAB_ID
  } catch { return true }
}

async function acknowledgeBrowserNotification(ids: string[]) {
  if (!ids.length) return
  await json<{ ok: boolean; marked: number }>("/v1/drafts/browser-notified", {
    method: "POST", body: JSON.stringify({ draft_ids: ids }),
  })
}

function showNotification(title: string, body: string, tag: string) {
  const item = new Notification(title, { body, tag })
  item.onclick = () => {
    window.focus()
    item.close()
  }
}

async function processPending() {
  const data = await json<{ drafts: PendingDraft[] }>("/v1/drafts/pending?limit=200")
  const drafts = data.drafts || []
  const seen = readSeen()
  if (localStorage.getItem(INITIALIZED_KEY) !== "1") {
    for (const draft of drafts) seen.add(draft.id)
    writeSeen(seen)
    localStorage.setItem(INITIALIZED_KEY, "1")
    if (drafts.length) {
      const newest = drafts[0]
      showNotification(
        `Черновики ждут подтверждения: ${drafts.length}`,
        `${label(newest)} · ${preview(newest.body)}`,
        "userio-draft-backlog",
      )
      await acknowledgeBrowserNotification(drafts.map((draft) => draft.id))
    }
    return
  }

  const fresh = drafts.filter((draft) => !seen.has(draft.id)).sort((a, b) => a.created_at - b.created_at)
  if (!fresh.length) return
  for (const draft of fresh) seen.add(draft.id)
  writeSeen(seen)

  if (fresh.length === 1) {
    const draft = fresh[0]
    showNotification("Черновик ждёт подтверждения", `${label(draft)} · ${preview(draft.body)}`, `userio-draft-${draft.id}`)
  } else {
    const newest = fresh[fresh.length - 1]
    showNotification(`Новые черновики ждут подтверждения: ${fresh.length}`, `${label(newest)} · ${preview(newest.body)}`, `userio-drafts-${newest.id}`)
  }
  await acknowledgeBrowserNotification(fresh.map((draft) => draft.id))
}

function installPermissionPrompt(onGranted: () => void) {
  if (document.getElementById(PROMPT_ID) || Notification.permission !== "default") return
  const button = document.createElement("button")
  button.id = PROMPT_ID
  button.type = "button"
  button.textContent = "🔔 Включить уведомления о черновиках"
  Object.assign(button.style, {
    position: "fixed", right: "16px", top: "16px", zIndex: "2147483647",
    border: "1px solid rgba(127,127,127,.35)", borderRadius: "999px", padding: "9px 14px",
    background: "rgba(24,37,51,.96)", color: "white", font: "500 13px system-ui, sans-serif",
    boxShadow: "0 8px 28px rgba(0,0,0,.25)", cursor: "pointer",
  })
  button.onclick = async () => {
    const permission = await Notification.requestPermission()
    button.remove()
    if (permission === "granted") onGranted()
  }
  document.body.appendChild(button)
}

export function startDraftBrowserNotifications() {
  if (!("Notification" in window) || !window.isSecureContext) return
  let stopped = false
  const tick = async () => {
    if (stopped) return
    try {
      if (!ownsNotifierLease()) return
      const { capabilities } = await json<{ capabilities: Capabilities }>("/v1/preferences/capabilities")
      if (!capabilities?.subscribe) return
      if (Notification.permission === "default") {
        installPermissionPrompt(() => { void processPending() })
        return
      }
      if (Notification.permission === "granted") await processPending()
    } catch {
      // Notifications are best-effort; the inbox itself remains authoritative.
    } finally {
      if (!stopped) window.setTimeout(() => { void tick() }, POLL_MS)
    }
  }
  void tick()
  return () => { stopped = true }
}
